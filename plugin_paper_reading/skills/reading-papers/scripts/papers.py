"""The paper-reading plugin's scripts (docs/adr/0033): a library of published screen papers read into Pertura's
knowledge base.

    papers.py init <library> [--exclusions FILE] [--knowledge-base FOLDER]
    papers.py register <library> <DOI | PubMed ID | PMC ID>...
    papers.py register <library> --pdf FILE --label "<first author> <year>" [--doi DOI]
    papers.py fetch <library> --list
    papers.py fetch <library> <key>...
    papers.py fetch <library> <key> --code <GitHub or Zenodo URL> [--commit SHA]
    papers.py cut <library> <key>
    papers.py modules <library> [--knowledge-base FOLDER]
    papers.py batch <library> <name> --reading <figures | moves> --keys k1,k2,...
    papers.py start <library> <key> --reading <reading> [--pipeline P | --figures N]
    papers.py spent <library> <batch> <key> <n> <tokens>
    papers.py agree <library> <key> [--reading figures]
    papers.py check <library> <key> [--pipeline P] [--figures N]
    papers.py show <library> <key> [--rows N]
    papers.py merge <library>
    papers.py count <library> <moves | chain | methods | code | figures> [--by <field>]...
    papers.py review <library> --reading <figures | moves> [--keys k1,k2,...] --out <library>/<file>.html
    papers.py check-draft <library> <draft>
    papers.py write-base <library> [--knowledge-base FOLDER] [--check]

The standard library only; reading a PDF needs pypdf. The network is used to look papers up (NCBI's E-utilities, the
bioRxiv API) on register, and to download (Europe PMC, bioRxiv, GitHub, Zenodo) on fetch; never otherwise.

The moves reading runs in pipelines: in each, a reader writes rows/<key>/pipe_<p>/moves.md and design.md, and a fresh
verifier writes verdicts.md and verified.md beside them. `modules` writes the module list the readers map moves to,
from the knowledge base's module entries; `agree` matches two pipelines' verified rows of one paper and measures them
against the pass line; `check --pipeline P` checks pipeline P's verified rows as the paper's moves, the ones that
merge.

The figures reading reads a paper once, and one paper in five twice: in each reading, a reader writes
rows/<key>/figures_<n>/figures.md. `agree --reading figures` checks both, cuts their rows into panels, measures how
far they agree on each panel's evidence, source and chart, and holds two readings started under one fixed version to
the pass line. `check --figures N` checks reading N's rows as the paper's figures, the ones that merge.

A reading is started with `start` before its reader is launched: it records the version of the reading's briefs and
lists beside where its rows go. A reading that has fixed versions (VERSIONS) starts only under one, and only its rows
read under one are merged. `batch` plans a batch before it runs (its papers, those read twice, its estimate and the
limit it stops before), and `spent` records what each reading took. `write-base` writes the parts of the knowledge
base that code writes, counts and lists from the catalogue, between the markers the base's files hold.

`review` writes one static HTML page for the review of a reading's calibration batch (ADR 0033, amendment 1, A1):
the papers and their measures, where each paper's two readings disagree, the rows under `other (...)`, each closed list
with its counts and examples, the grain, and two rows per paper beside their text. It is built from the rows, the
checks and the agreement files, and refuses a paper whose rows changed after them. The page holds quotes from the
papers, so it is written inside the library, as an .html file beside its folders, never in place of a file that
is not an earlier review page.

An exclusion list is a JSON object of groups. Each group may hold "ids" (GEO series, PMC or PubMed IDs), "dois",
"names" (first authors) and "names_with_years" ([name, year] pairs). A paper on it is not registered; a text that names
a listed series yields no cut text; a row or a draft that cites a listed paper fails its check.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

FOLDERS = ("sources", "texts", "rows", "checks", "catalogue", "drafts")
PAPER_FIELDS = ("key", "label", "doi", "pmid", "pmcid", "source", "text_url", "state", "why", "code_url",
                "code_commit")
READINGS = ("moves", "chain", "methods", "code", "figures")
# the labels the readers' briefs list (agents/*.md); a test keeps the two the same
KINDS = ("modules or programmes", "link to genetics, atlases or patients", "mechanism from outside data",
         "states or composition", "condition or context by perturbation", "similar targets or clusters",
         "co-functional regulator sets", "relation across targets", "guide or replicate agreement",
         "combinations or epistasis", "cell-type specificity", "enhancer to gene (cis)",
         "trajectory, pseudotime or density", "dose or titration", "crispra against crispri", "other")
STEPS = ("guide assignment", "cell quality control", "doublets and multiplets", "normalisation", "batch correction",
         "cells kept by perturbation", "minimum cells per target or guide", "the test of perturbation effects",
         "multiple testing and hit thresholds", "programmes or modules", "similarity of perturbations", "composition",
         "enrichment", "outside data", "other")
DATA = ("screen data", "other data of the authors", "outside data", "literature")
AS = ("used", "also tried")
FIELDS = {"moves": ("paper", "panel", "trigger", "question", "cells", "compared_with", "unit", "method", "readout",
                    "showed", "kind", "module", "relation", "decided_by", "evidence", "result_evidence"),
          "chain": ("step", "trigger", "question", "analysis", "data", "showed", "figure", "composed"),
          "methods": ("paper", "step", "as", "choice", "package", "parameters", "reason", "section"),
          "code": ("paper", "step", "file", "line", "call", "parameters"),
          "figures": ("paper", "figure", "panel", "conclusion_quote", "evidence", "source", "chart", "marks",
                      "targets_shown", "colour", "colour_named_by", "image", "readout", "compared_with", "n_stated",
                      "evidence_quote")}
# the figures reading's version 1 read the legends only, without the five fields its second version reads from the
# images; its rows are still checked, compared and merged as version 1's
FIGURES_V1 = ("paper", "figure", "panel", "conclusion_quote", "evidence", "source", "chart", "readout",
              "compared_with", "n_stated", "evidence_quote")
# the moves reading's first version wrote eight fields; such a row is still checked by that version's checks (which
# a row of the current version passes too), so that readings of either version can be compared, but a library's
# check fails it
MOVES_FIRST_VERSION = ("paper", "panel", "trigger", "question", "analysis", "showed", "kind", "decided_by")
UNITS = ("cells", "targets", "guides", "genes", "units", "pairs")
RELATIONS = ("default", "method", "question", "method and question", "none")
QUOTES = ("evidence", "result_evidence")
QUOTE_WORDS = (8, 40)
DESIGN = {"modality": ("CRISPR knockout", "CRISPRi", "CRISPRa", "CRISPRi and CRISPRa", "base or prime editing",
                       "other"),
          "system": ("cell line", "primary cells", "organoid or stem-cell derived", "in vivo"),
          "guides per cell": ("one", "pairs or combinations", "several (high MOI)", "not stated"),
          "conditions": ("none", "stimulation or drug", "time points", "several cell types or tissues",
                         "more than one of these"),
          "readout": ("transcriptome", "transcriptome and protein", "transcriptome and chromatin", "other"),
          "replicates": ("biological (donors, animals or separate cultures)", "technical only", "not stated")}
VERDICT_FIELDS = ("row", "verdict", "changed", "reason", "quote")
VERDICTS = ("accept", "fix", "delete")
PIPELINES = (1, 2)
# the pass line for two pipelines' readings of one paper: each measure at least this
PASS = {"quote_check": 0.95, "row_agreement": 0.70, "module_agreement": 0.75}
CONTENT_MIN = 0.15  # the content words' Jaccard index two rows need to be matched
COUNT_BY = {"moves": ["module"]}  # what a count is by when no field is named
# the fields whose lists end with "other (...)", counted as one value "other"
COUNT_AS_LABELS = {"moves": ("kind",), "methods": ("step",), "code": ("step",),
                   "figures": ("evidence", "chart", "marks", "colour")}
# the figure reader's lists (agents/figure-reader.md), in its order; the order of the evidence is its precedence
FIGURE_EVIDENCE = ("the design", "prior knowledge", "a model of the findings", "a target's own gene",
                   "a method's benchmark", "a pair against its singles", "a check by guide or replicate",
                   "cells, guides and quality", "across conditions, cell types or doses", "similar targets",
                   "gene-set meaning", "gene programs or modules found", "a program or protein per cell",
                   "a trajectory or pseudotime", "states or composition", "effects across many targets",
                   "a target's effect on nearby genes (cis)", "one target's effect on genes",
                   "a model fitted to the screen", "a phenotype", "other")
FIGURE_SOURCES = ("the screen", "a follow-up experiment", "the paper's other data", "outside data", "simulated data",
                  "none")
FIGURE_CHARTS = ("schematic", "embedding", "density on an embedding", "heatmap centred on zero", "correlation heatmap",
                 "heatmap, one-sided scale", "heatmap, scale not stated", "box or violin with points", "box or violin",
                 "distribution", "bar per group", "bar of shares", "ranked dots", "dot plot",
                 "scatter with correlation", "scatter", "volcano", "line or curve", "network", "overlap diagram",
                 "enrichment plot", "image or cytometry plot", "table", "not stated", "other")
# the second version's fields, read from a main figure's image (agents/figure-reader.md)
FIGURE_MARKS = ("a cell", "a guide", "a target", "a gene", "a sample, animal or donor", "a term or gene set",
                "a summary of a group", "none", "other")
FIGURE_TARGETS = ("every target tested", "a group of targets", "one or a few targets", "no target")
FIGURE_COLOURS = ("groups", "a value on a scale", "signed values on a diverging scale", "one colour", "other")
FIGURE_COLOUR_NAMED_BY = ("a key", "labels on or beside the marks", "a colour bar", "the legend text only", "nowhere",
                          "not applicable")
NO_IMAGE = "none"  # a figure read from its legend alone: an Extended Data figure, or one whose image is missing
PDF_PAGE = re.compile(r"page \d+")  # a figure read from a page of the paper's PDF
# a chart the legend does not name, inferred in the two cases the brief gives (a correlation stated, "as in (X)"), is
# a chart of the list written with this suffix; `not stated` and `other (...)` are never inferred
INFERRED = " (inferred)"
NOT_INFERRED = ("not stated", "other")
# the figures reading's first version (its pilot) gave one field, kind, for where a panel's data come from and what it
# shows; its kinds are the evidence's, with three that are now sources, without the five added since (gene programs
# found, a trajectory or pseudotime, a target's effect on nearby genes, a model fitted to the screen, a phenotype),
# and in its own words ("programme"). Such a row of ten fields is still checked by that version's checks, so that
# readings of either version can be compared, but a library's check fails it
FIGURES_FIRST_VERSION = ("paper", "figure", "panel", "conclusion_quote", "kind", "chart", "readout", "compared_with",
                         "n_stated", "evidence_quote")
FIGURE_KINDS_FIRST_VERSION = ("the design", "prior knowledge", "a model of the findings", "a target's own gene",
                              "an independent experiment", "the paper's other data", "outside data",
                              "a method's benchmark", "a pair against its singles", "a check by guide or replicate",
                              "cells, guides and quality", "across conditions, cell types or doses",
                              "similar targets", "gene-set meaning", "a programme or protein per cell",
                              "states or composition", "effects across many targets", "one target's effect on genes",
                              "other")
FIGURE_READINGS = (1, 2)
FIGURE_LABELS = ("evidence", "source", "chart")  # what two readings of a paper's figures are compared on
FIGURE_LABELS_V2 = FIGURE_LABELS + ("marks", "targets_shown", "colour", "colour_named_by")  # and from the images
FIGURE_LABELS_FIRST_VERSION = ("kind", "chart")
CONCLUSION_WORDS = (2, 50)
NO_CONCLUSION = ("", "-", "none")  # a figures row that gives no conclusion, any dash as "-"
# the pass line for two readings of one paper's figures, under one fixed version: each measure at least this
FIGURE_PASS = {"evidence_agreement": 0.75, "source_agreement": 0.85, "chart_agreement": 0.75, "quote_check": 0.95,
               "conclusion_check": 0.95}
# the second version's, set before its calibration (docs/build/figure_reading_v2_from_the_images_draft_2026-11-11.md)
FIGURE_PASS_V2 = {**FIGURE_PASS, "marks_agreement": 0.75, "targets_shown_agreement": 0.75, "colour_agreement": 0.75,
                  "colour_named_by_agreement": 0.75}
# a reading's briefs (agents/*.md) and the lists its rows are checked against: together, the reading's version
BRIEFS = {"moves": ("moves-reader.md", "moves-verifier.md"), "chain": ("chain-reader.md",),
          "methods": ("methods-reader.md",), "code": ("code-reader.md",), "figures": ("figure-reader.md",)}
LISTS = {"moves": (KINDS, UNITS, RELATIONS, DESIGN, VERDICTS), "chain": (DATA,), "methods": (STEPS, AS),
         "code": (STEPS,), "figures": (FIGURE_EVIDENCE, FIGURE_SOURCES, FIGURE_CHARTS, FIGURE_MARKS, FIGURE_TARGETS,
                                       FIGURE_COLOURS, FIGURE_COLOUR_NAMED_BY, NO_IMAGE, PDF_PAGE.pattern)}
# the fixed versions of a reading, by the sha256 of its briefs and lists (ADR 0033, amendment 1). A reading that has
# them starts only under one, and only its rows read under one are merged. A change to its briefs or lists is a new
# version: v1.1 where it only adds (a kind for rows under other (...), a word that names a chart), v2 where it moves
# panels from one value to another (docs/build/reading_papers/the_procedure.md)
# v2, which reads the main figures from their images, was recorded on 2026-11-11 for its calibration: ten papers read
# twice and held to FIGURE_PASS_V2, then reviewed with the user
VERSIONS = {"figures": {"v1": "beedcbd66e77671d063487cb102e332f022ab353d7880ffdfe11cfd23d0b7c8d",
                        "v2": "437a201e29fad19b76cd42081f249e7589532da04317120378c4ed61e8fb6611"}}
VERSION_FILE = "version.json"
# the tokens of Sonnet one reading takes, from the trials: a figures reading; a moves pipeline, reader and verifier
COST = {"figures": 100_000, "moves": 190_000}
OVER = 1.5  # a batch stops before it would pass this many times its estimate
TWICE_EVERY = 5  # the first paper of every five in a batch is read twice: the drift check
CHAIN_FIELDS = ("paper", "question", "design", "depth")
CODE_FILES = (".py", ".r", ".rmd", ".qmd", ".ipynb", ".sh", ".jl", ".m", ".nf", ".smk", ".wdl", ".md", ".txt",
              ".yaml", ".yml", ".json", ".toml", ".cfg")
CODE_NAMES = ("Snakefile", "Makefile", "Dockerfile")
CODE_FILE_MAX = 5_000_000  # larger files in a repository are data, which the code reader does not need
DOWNLOAD_MAX = 1_000_000_000
AGENT = "pertura-reader/0.3"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
STOP = set("that this with from which their they were been have into than them these those also only what when where "
           "because after before among about each other most more such some there then through while within".split())


class LibraryError(Exception):
    pass


# -- the library ------------------------------------------------------------------------------------------------------

def write_text(path, text):
    """Through a temporary file, so a failure leaves no half-written file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def settings(lib):
    path = os.path.join(lib, "reading.json")
    if not os.path.exists(path):
        raise LibraryError(f"{lib} is not a library; start one with: papers.py init {lib}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def papers(lib):
    settings(lib)
    with open(os.path.join(lib, "papers.tsv"), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def save_papers(lib, rows):
    out = io.StringIO()
    w = csv.DictWriter(out, PAPER_FIELDS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    write_text(os.path.join(lib, "papers.tsv"), out.getvalue())


def paper(rows, key):
    for r in rows:
        if r["key"] == key:
            return r
    raise LibraryError(f"no paper {key} in the library; register it first")


def init(lib, exclusions=None, knowledge_base=None):
    if os.path.exists(os.path.join(lib, "reading.json")):
        raise LibraryError(f"{lib} is a library already")
    for path, what in ((exclusions, "exclusion list"), (knowledge_base, "knowledge base")):
        if path and not os.path.exists(path):
            raise LibraryError(f"no {what} at {path}")
    for f in FOLDERS:
        os.makedirs(os.path.join(lib, f), exist_ok=True)
    write_text(os.path.join(lib, "reading.json"), json.dumps(
        {"exclusions": os.path.abspath(exclusions) if exclusions else None,
         "knowledge_base": os.path.abspath(knowledge_base) if knowledge_base else None}, indent=1) + "\n")
    save_papers(lib, [])
    return f"library started at {lib}" + (f"; exclusion list {exclusions}" if exclusions else "; no exclusion list")


# -- the exclusion list -----------------------------------------------------------------------------------------------

class KeptOut:
    def __init__(self, data=None):
        self.series, self.ids, self.dois, self.names, self.pairs = set(), set(), set(), [], []
        for group in (data or {}).values():
            if not isinstance(group, dict):
                continue
            for i in group.get("ids", []):
                (self.series if re.fullmatch(r"GSE\d+", i) else self.ids).add(i)
            self.dois.update(d.lower() for d in group.get("dois", []))
            self.names += [n for n in group.get("names", []) if n not in self.names]
            self.pairs += [(n, int(y)) for n, y in group.get("names_with_years", [])]

    @classmethod
    def of(cls, lib):
        path = settings(lib).get("exclusions")
        if not path:
            return cls()
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def paper(self, rec):
        """Why a paper is kept out, or ""."""
        if rec.get("doi") and rec["doi"].lower() in self.dois:
            return f"its DOI {rec['doi']} is on the exclusion list"
        for k in ("pmid", "pmcid"):
            if rec.get(k) and rec[k] in self.ids:
                return f"its ID {rec[k]} is on the exclusion list"
        name, year = rec.get("first_author", ""), rec.get("year", "")
        if name and name in self.names:
            return f"its first author, {name}, is on the exclusion list"
        if name and year.isdigit() and (name, int(year)) in self.pairs:
            return f"{name} {year} is on the exclusion list"
        return ""

    def series_in(self, text):
        return sorted(s for s in self.series if re.search(rf"\b{s}\b", text))

    def cited_in(self, text):
        """The listed papers and series a text cites, in any of their forms."""
        low = text.lower()
        found = [d for d in sorted(self.dois) if d in low]
        found += [i for i in sorted(self.ids) if re.search(rf"(?:(?<![\dA-Za-z])|(?<=PMID)){i}(?!\d)", text)]
        found += self.series_in(text)
        words = lambda n: re.escape(n).replace("\\ ", r"\s+")  # a citation may be wrapped over two lines
        found += [n for n in self.names if re.search(rf"\b{words(n)}\b", text)]
        found += [f"{n} {y}" for n, y in self.pairs if re.search(rf"\b{words(n)}{BETWEEN}{y}[a-z]?\b", text)]
        return found


# a citation between a name and its year: "et al" (with a period or not, in italics or not), a second author, or a
# journal's words ("Doe et al. (2026)", "Doe and Roe, 2026", "Doe et al., Nat Commun 2026b")
BETWEEN = r"(?:\s*[*_]*\s*et\s+al\.?[*_]*)?(?:\s+(?:and|&)\s+[A-Z][\w'-]+)?(?:,?\s+(?:[A-Z][\w.&-]*\s+){0,6})?,?\s*\(?"


# -- the network ------------------------------------------------------------------------------------------------------

def get(url, timeout=60):
    """A URL's body as bytes; None when the server has no such page."""
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        raise


def download(url, path, cap=DOWNLOAD_MAX, timeout=120):
    """A URL to a file, in pieces, refusing more than cap bytes; False when the server has no such page."""
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(path + ".part", "wb") as out:
            size = 0
            while True:
                piece = r.read(1 << 20)
                if not piece:
                    break
                size += len(piece)
                if size > cap:
                    raise LibraryError(f"{url} is larger than {cap:,} bytes; not downloaded")
                out.write(piece)
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return False
        raise
    finally:
        if os.path.exists(path + ".part") and not os.path.getsize(path + ".part"):
            os.remove(path + ".part")
    os.replace(path + ".part", path)
    return True


def eutils(what, **params):
    params.update(retmode="json", tool="pertura-reader")
    time.sleep(0.4)  # NCBI asks for no more than three requests a second without a key
    body = get(EUTILS + what + ".fcgi?" + urllib.parse.urlencode(params))
    return json.loads(body) if body else {}


def last_name(author):
    """ "Zhao Y" -> "Zhao"; "Van Dijk MA" -> "Van Dijk"; "de la Cruz A" -> "de la Cruz"."""
    parts = author.split()
    if len(parts) > 1 and re.fullmatch(r"[A-Z]{1,3}", parts[-1]):
        parts = parts[:-1]
    return " ".join(parts)


def biorxiv(doi):
    """The latest version's details from the bioRxiv API, or {}."""
    body = get(f"https://api.biorxiv.org/details/biorxiv/{doi}/na/json")
    versions = (json.loads(body).get("collection") or []) if body else []
    return versions[-1] if versions else {}


def look_up(given):
    """A paper's IDs, first author, year and open text, from a DOI, a PubMed ID or a PMC ID."""
    given = given.strip()
    rec = {"doi": "", "pmid": "", "pmcid": "", "first_author": "", "year": "", "source": "none", "text_url": "",
           "published": ""}
    if re.fullmatch(r"(?i)pmc\d+", given):
        rec["pmcid"] = given.upper()
        item = eutils("esummary", db="pmc", id=given[3:]).get("result", {}).get(given[3:], {})
        for a in item.get("articleids", []):
            if a.get("idtype") == "pmid" and a.get("value", "0") != "0":
                rec["pmid"] = a["value"]
            elif a.get("idtype") == "doi":
                rec["doi"] = a.get("value", "")
    elif re.fullmatch(r"\d+", given):
        rec["pmid"] = given
    elif re.match(r"(?i)(https?://(dx\.)?doi\.org/)?10\.", given):
        rec["doi"] = re.sub(r"(?i)^https?://(dx\.)?doi\.org/", "", given)
        found = eutils("esearch", db="pubmed", term=f"{rec['doi']}[doi]").get("esearchresult", {}).get("idlist", [])
        if len(found) == 1:
            rec["pmid"] = found[0]
    else:
        raise LibraryError(f"{given}: not a DOI, a PubMed ID or a PMC ID")
    if rec["pmid"]:
        item = eutils("esummary", db="pubmed", id=rec["pmid"]).get("result", {}).get(rec["pmid"], {})
        if not item or "error" in item:
            raise LibraryError(f"{given}: PubMed has no record {rec['pmid']}")
        for a in item.get("articleids", []):
            if a.get("idtype") == "doi" and not rec["doi"]:
                rec["doi"] = a.get("value", "")
            elif a.get("idtype") in ("pmc", "pmcid") and not rec["pmcid"]:
                m = re.search(r"PMC\d+", a.get("value", ""))
                rec["pmcid"] = m.group(0) if m else ""
        authors = item.get("authors") or []
        rec["first_author"] = last_name(authors[0].get("name", "")) if authors else ""
        year = re.search(r"\d{4}", item.get("pubdate", "") or item.get("epubdate", ""))
        rec["year"] = year.group(0) if year else ""
    if rec["pmcid"]:
        rec["source"] = "pmc"
        rec["text_url"] = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{rec['pmcid']}/fullTextXML"
    elif rec["doi"].startswith("10.1101/"):
        b = biorxiv(rec["doi"])
        if b:
            rec["source"] = "biorxiv"
            rec["text_url"] = b.get("jatsxml", "")
            rec["first_author"] = rec["first_author"] or b.get("authors", "").split(";")[0].split(",")[0].strip()
            rec["year"] = rec["year"] or b.get("date", "")[:4]
            rec["published"] = "" if b.get("published", "NA") == "NA" else b["published"]
    if not (rec["pmid"] or rec["pmcid"] or rec["source"] == "biorxiv"):
        raise LibraryError(f"{given}: neither PubMed nor bioRxiv has it")
    return rec


def key_and_label(rec):
    name_year = " ".join(x for x in (rec.get("first_author", ""), rec.get("year", "")) if x)
    if rec.get("pmid"):
        return "pmid" + rec["pmid"], f"PMID{rec['pmid']} {name_year}".strip()
    if rec.get("pmcid"):
        return rec["pmcid"].lower(), f"{rec['pmcid']} {name_year}".strip()
    return "doi-" + re.sub(r"[^a-z0-9]+", "-", rec["doi"].lower()).strip("-"), f"DOI {rec['doi']} {name_year}".strip()


def register(lib, given, pdf=None, label=None, doi=None):
    kept, rows = KeptOut.of(lib), papers(lib)
    known = {r["key"]: r for r in rows}
    out = []
    if pdf:
        m = re.fullmatch(r"\s*(.+?)\s+(\d{4})\s*", label or "")
        if not m:
            raise LibraryError('a PDF needs --label "<first author> <year>"')
        rec = {"doi": doi or "", "first_author": m.group(1), "year": m.group(2)}
        key = "pdf-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        if key in known:
            out.append(f"{key}: {known[key]['label']}, registered before ({known[key]['state']})")
        else:
            why = kept.paper(rec)
            row = dict.fromkeys(PAPER_FIELDS, "")
            row.update(key=key, label=label.strip(), doi=rec["doi"], source="pdf",
                       state="kept out" if why else "fetched", why=why)
            if not why:
                os.makedirs(os.path.join(lib, "sources", key), exist_ok=True)
                shutil.copyfile(pdf, os.path.join(lib, "sources", key, "paper.pdf"))
            rows.append(row)
            known[key] = row
            out.append(f"{key}: {row['label']}, " + (f"kept out: {why}" if why else "its PDF is in the library"))
    for g in given:
        rec = look_up(g)
        key, lab = key_and_label(rec)
        if key in known:
            out.append(f"{key}: {known[key]['label']}, registered before ({known[key]['state']})")
            continue
        why = kept.paper(rec)
        row = dict.fromkeys(PAPER_FIELDS, "")
        row.update({k: rec[k] for k in ("doi", "pmid", "pmcid", "source", "text_url")})
        row.update(key=key, label=lab, state="kept out" if why else "registered",
                   why=why or (f"a preprint published as {rec['published']}" if rec["published"] else ""))
        rows.append(row)
        known[key] = row
        if why:
            out.append(f"{key}: {lab}, kept out: {why}")
        else:
            text = {"pmc": "open full text at Europe PMC", "biorxiv": "full text at bioRxiv"}.get(
                rec["source"], "no open full text known; its PDF is needed")
            out.append(f"{key}: {lab}, registered; {text}" + (f"; {row['why']}" if row["why"] else ""))
    save_papers(lib, rows)
    return out


# -- fetching ---------------------------------------------------------------------------------------------------------

def repositories(text):
    """The code repositories a text names: GitHub repositories and Zenodo records."""
    found = []
    for m in re.finditer(r"github\.com/([\w.-]+)/([\w.-]+)", text):
        repo = re.sub(r"(\.git)?[.,;)]*$", "", m.group(2))
        url = f"https://github.com/{m.group(1)}/{repo}"
        if url not in found:
            found.append(url)
    for m in re.finditer(r"zenodo\.org/records?/(\d+)|10\.5281/zenodo\.(\d+)", text, re.I):
        url = f"https://zenodo.org/records/{m.group(1) or m.group(2)}"
        if url not in found:
            found.append(url)
    return found


def code_root(lib, key):
    """The repository's folder: the archive's one top folder where it has one."""
    base = os.path.join(lib, "sources", key, "code")
    if not os.path.isdir(base):
        return ""
    entries = os.listdir(base)
    if len(entries) == 1 and os.path.isdir(os.path.join(base, entries[0])):
        return os.path.join(base, entries[0])
    return base


def unpack(archive, dest):
    """The code files of a zip archive, kept inside dest: no path that leaves it, no file larger than a script."""
    kept = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if info.is_dir() or not parts or name.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", name):
                continue
            if not (name.lower().endswith(CODE_FILES) or parts[-1] in CODE_NAMES) or info.file_size > CODE_FILE_MAX:
                continue
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            kept += 1
    return kept


def fetch_list(lib):
    out = []
    for r in papers(lib):
        if r["state"] == "registered":
            out.append(f"{r['key']} ({r['label']}): " + (r["text_url"] or "no open full text known; the user can give "
                                                                           f"its PDF as sources/{r['key']}/paper.pdf"))
        if r["pmcid"] and r["state"] != "kept out" and not os.path.exists(
                os.path.join(lib, "sources", r["key"], FIGURE_IMAGES)):
            out.append(f"{r['key']} ({r['label']}): its main figures' images, from PMC's open data "
                       f"({OPEN_DATA}), with --figure-images")
        avail = os.path.join(lib, "texts", r["key"], "availability.txt")
        if r["state"] in ("cut", "checked", "merged") and not r["code_commit"] and os.path.exists(avail):
            named = repositories(read_text(avail))
            if named:
                out.append(f"{r['key']} ({r['label']}): code named in the paper: {', '.join(named)}")
    return out or ["nothing to fetch"]


def fetch(lib, keys, code=None, commit=None, figure_images=False):
    rows, out = papers(lib), []
    for key in keys:
        r = paper(rows, key)
        if r["state"] == "kept out":
            raise LibraryError(f"{key} is kept out: {r['why']}")
        if code:
            out.append(fetch_code(lib, r, code, commit))
            continue
        if figure_images:
            out.append(fetch_figure_images(lib, r))
            continue
        if not r["text_url"]:
            out.append(f"{key}: no open full text known; its PDF goes in sources/{key}/paper.pdf")
            continue
        folder = os.path.join(lib, "sources", key)
        path = os.path.join(folder, "fulltext.xml")
        os.makedirs(folder, exist_ok=True)
        if os.path.exists(path):
            r["state"] = r["state"] if r["state"] != "registered" else "fetched"
            out.append(f"{key}: fetched before")
            continue
        urls = [r["text_url"]] + ([f"{EUTILS}efetch.fcgi?db=pmc&id={r['pmcid'][3:]}&tool=pertura-reader"]
                                  if r["pmcid"] else [])
        tried = []
        for url in urls:
            try:
                if download(url, path) and has_body(path):
                    break
                tried.append(f"{url}: no full text")
            except (urllib.error.URLError, OSError, LibraryError) as e:
                tried.append(f"{url}: {e}")
            if os.path.exists(path):
                os.replace(path, path + ".without_body")  # kept for the record, never read as the text
        if os.path.exists(path):
            r["state"] = "fetched"
            out.append(f"{key}: full text fetched")
        else:
            out.append(f"{key}: no open full text ({'; '.join(tried)}); its PDF goes in sources/{key}/paper.pdf")
        save_papers(lib, rows)
    save_papers(lib, rows)
    return out


# -- the main figures' images -----------------------------------------------------------------------------------------

FIGURE_IMAGES = "figures.tsv"  # in a paper's sources folder: each main figure's image file, from PMC's open data
OPEN_DATA = "https://pmc-oa-opendata.s3.amazonaws.com/"  # PMC's article datasets on AWS: texts, PDFs and media
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".gif")
XLINK = "{http://www.w3.org/1999/xlink}href"


def main_figure_graphics(path):
    """[(figure, graphic)] of a JATS file's main figures, in order: each <fig> whose label names one main figure, with
    its graphic's name without its extension."""
    out = []
    for fig in ET.parse(path).getroot().iter("fig"):
        label, graphic = fig.find("label"), fig.find(".//graphic")
        named = figures("".join(label.itertext())) if label is not None else []
        href = graphic.get(XLINK) if graphic is not None else None
        if len(named) == 1 and named[0][0] == "main" and href:
            out.append((named[0], os.path.splitext(href.rsplit("/", 1)[-1])[0]))
    return out


def open_data_media(pmcid):
    """{image name without its extension, lower case: (url, md5)} of an article's images in PMC's open data, from the
    record of its latest version; {} when the open data hold no record of it."""
    listing = (get(f"{OPEN_DATA}?list-type=2&prefix=metadata/{pmcid}.") or b"").decode("utf-8", "replace")
    keys = re.findall(r"<Key>(metadata/[^<]+?\.(\d+)\.json)</Key>", listing)
    if not keys:
        return {}
    record = json.loads(get(OPEN_DATA + max(keys, key=lambda k: int(k[1]))[0]) or b"{}")
    out = {}
    for s3 in record.get("media_urls") or []:
        m = re.fullmatch(r"s3://pmc-oa-opendata/([^?]+)(?:\?md5=([0-9a-f]+))?", s3)
        if m and m.group(1).lower().endswith(IMAGE_TYPES):
            name = m.group(1).rsplit("/", 1)[-1]
            out[os.path.splitext(name)[0].lower()] = (OPEN_DATA + m.group(1), m.group(2) or "")
    return out


def fetch_figure_images(lib, r):
    """A paper's main-figure images from PMC's open data into sources/<key>/figures/, each checked against its md5,
    and their list, sources/<key>/figures.tsv (figure, file, bytes, md5, url), which the figure reader is given."""
    key = r["key"]
    folder = os.path.join(lib, "sources", key)
    xml = os.path.join(folder, "fulltext.xml")
    if not r["pmcid"] or not os.path.exists(xml):
        return f"{key}: no figure images: " + ("it has no PMC copy" if not r["pmcid"] else "fetch its full text first")
    if os.path.exists(os.path.join(folder, FIGURE_IMAGES)):
        return f"{key}: figure images fetched before"
    graphics = main_figure_graphics(xml)
    media = open_data_media(r["pmcid"]) if graphics else {}
    rows, missing = [], []
    for fig, graphic in graphics:
        found = media.get(graphic.lower())
        if not found:
            missing.append(figure_name(fig))
            continue
        url, md5 = found
        name = url.rsplit("/", 1)[-1]
        path = os.path.join(folder, "figures", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not download(url, path):
            missing.append(figure_name(fig))
            continue
        with open(path, "rb") as fh:
            digest = hashlib.md5(fh.read()).hexdigest()
        if md5 and digest != md5:
            os.remove(path)
            raise LibraryError(f"{key}: {name} does not match the md5 PMC's open data give it; it was removed")
        rows.append({"figure": figure_name(fig), "file": name, "bytes": os.path.getsize(path), "md5": digest,
                     "url": url})
    buf = io.StringIO()
    w = csv.DictWriter(buf, ("figure", "file", "bytes", "md5", "url"), delimiter="\t", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    write_text(os.path.join(folder, FIGURE_IMAGES), buf.getvalue())
    return (f"{key}: the images of {len(rows)} of its {len(graphics)} main figures, "
            f"{sum(x['bytes'] for x in rows) / 1e6:.1f} MB" +
            (f"; not in PMC's open data: {', '.join(missing)}" if missing else ""))


def has_body(path):
    """Whether a JATS file holds the article's body, not its front matter alone."""
    try:
        return ET.parse(path).getroot().find(".//body") is not None
    except ET.ParseError:
        return False


def fetch_code(lib, r, url, commit=None):
    folder = os.path.join(lib, "sources", r["key"])
    os.makedirs(folder, exist_ok=True)
    gh = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    zen = re.search(r"zenodo\.org/records?/(\d+)|10\.5281/zenodo\.(\d+)", url, re.I)
    archives = []
    if gh:
        owner, repo = gh.groups()
        if not commit:
            info = json.loads(get(f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD") or b"{}")
            commit = info.get("sha", "")
            if not commit:
                raise LibraryError(f"{url}: GitHub gave no commit")
        path = os.path.join(folder, "code.zip")
        if not download(f"https://codeload.github.com/{owner}/{repo}/zip/{commit}", path):
            raise LibraryError(f"{url} at {commit}: not found")
        archives.append(path)
    elif zen:
        record = zen.group(1) or zen.group(2)
        info = json.loads(get(f"https://zenodo.org/api/records/{record}") or b"{}")
        commit = f"zenodo record {record}"
        for i, f in enumerate(info.get("files", [])):
            if f.get("key", "").lower().endswith(".zip"):
                path = os.path.join(folder, f"code_{i}.zip")
                if download(f["links"]["self"], path):
                    archives.append(path)
        if not archives:
            raise LibraryError(f"{url}: the record holds no zip archive")
    else:
        raise LibraryError(f"{url}: a GitHub repository or a Zenodo record")
    dest = os.path.join(folder, "code")
    if os.path.exists(dest):
        raise LibraryError(f"{dest} exists; this paper's code was fetched before")
    kept = sum(unpack(a, dest) for a in archives)
    r["code_url"], r["code_commit"] = url, commit
    return f"{r['key']}: code at {commit}, {kept} files, in {code_root(lib, r['key'])}"


# -- cutting ----------------------------------------------------------------------------------------------------------

INTRO = re.compile(r"^(introduction|background|main)$", re.I)
RESULTS = re.compile(r"results", re.I)
DISCUSSION = re.compile(r"^(discussion|conclusions?|summary|limitations)", re.I)
METHODS = re.compile(r"methods|materials|experimental (procedures|model)|key resources", re.I)
AVAILABILITY = re.compile(r"availability|accession", re.I)
SKIP = re.compile(r"author contributions|acknowledg|competing|declaration|funding|references|"
                  r"supplementary information$|ethics|footnotes|abbreviations", re.I)


def clean(s):
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def classify(sections):
    """[(title, text)] -> (results, methods, availability): the Results sections, or, when no section is titled
    Results, the sections between the introduction and the discussion or methods (papers that title results by
    finding)."""
    titled = any(RESULTS.search(t) for t, _ in sections)
    results, methods, availability = [], [], []
    phase = "before"
    for title, text in sections:
        if AVAILABILITY.search(title):
            availability.append((title, text))
        elif SKIP.search(title):
            continue
        elif METHODS.search(title):
            phase = "after"
            methods.append((title, text))
        elif DISCUSSION.search(title):
            phase = "after"
        elif INTRO.search(title) and not titled:
            phase = "results"
        elif (titled and RESULTS.search(title)) or (not titled and phase in ("before", "results") and
                                                       not INTRO.search(title)):
            results.append((title, text))
            phase = "results"
    return results, methods, availability


def from_jats(path):
    root = ET.parse(path).getroot()

    def text(el):
        parts = []
        for node in el.iter():
            if node.tag == "title" and node is not el:
                parts.append("\n## " + "".join(node.itertext()).strip() + "\n")
            elif node.tag == "p":
                parts.append("".join(node.itertext()).strip() + "\n")
        return clean("\n".join(parts))

    legends = []
    for fig in root.iter("fig"):
        label, cap = fig.find("label"), fig.find("caption")
        if cap is not None:
            legends.append(clean(("".join(label.itertext()).strip() + ". " if label is not None else "") +
                                 " ".join("".join(x.itertext()) for x in cap)))
    for sup in root.iter("supplementary-material"):
        cap = sup.find("caption")
        if cap is not None:
            legends.append(clean(" ".join("".join(x.itertext()) for x in cap)))
    raw = "".join(root.itertext())
    sections = []
    body = root.find(".//body")
    if body is not None:
        for sec in body.findall("sec"):
            for fig in sec.iter("fig"):  # legends are kept apart, above
                fig.clear()
            t = sec.find("title")
            sections.append(("".join(t.itertext()).strip() if t is not None else "", text(sec)))
    for sec in root.findall(".//back/sec") + root.findall(".//back//notes") + root.findall(".//back/fn-group"):
        t = sec.find("title")
        sections.append(("".join(t.itertext()).strip() if t is not None else "", text(sec)))
    return sections, legends, raw


PDF_HEADING = re.compile(r"^\s*(introduction|results|discussion|(star\W*)?methods|materials and methods|online methods|"
                         r"references|acknowledge?ments|(data|code|data and code|resource) availability)\s*$", re.I)


def from_pdf(path):
    try:
        import pypdf
    except ImportError:
        raise LibraryError("reading a PDF needs the pypdf package, which is not installed; ask the user")
    raw = "\n".join((page.extract_text() or "") for page in pypdf.PdfReader(path).pages)
    sections, title, lines = [], "", []
    for line in raw.split("\n"):
        if PDF_HEADING.match(line):
            sections.append((title, clean("\n".join(lines))))
            title, lines = line.strip(), []
        else:
            lines.append(line)
    sections.append((title, clean("\n".join(lines))))
    return sections, [], raw


def cut(lib, key):
    rows = papers(lib)
    r = paper(rows, key)
    if r["state"] == "kept out":
        raise LibraryError(f"{key} is kept out: {r['why']}")
    folder = os.path.join(lib, "sources", key)
    xml, pdf = os.path.join(folder, "fulltext.xml"), os.path.join(folder, "paper.pdf")
    if os.path.exists(xml):
        sections, legends, raw = from_jats(xml)
    elif os.path.exists(pdf):
        sections, legends, raw = from_pdf(pdf)
    else:
        raise LibraryError(f"{key} has no full text in {folder}; fetch it, or give its PDF as paper.pdf")
    named = KeptOut.of(lib).series_in(raw)
    if named:
        r["state"], r["why"] = "kept out", f"its text names {', '.join(named)}"
        save_papers(lib, rows)
        return f"{key}: kept out, its text names {', '.join(named)}; no text written"
    results, methods, availability = classify(sections)
    texts = os.path.join(lib, "texts", key)
    write_text(os.path.join(texts, "results.txt"), "\n".join(
        ["# Results"] + [f"\n# {t}\n\n{x}" for t, x in results] + ["\n# Figure and supplementary legends\n"] +
        [f"- {x}" for x in legends]) + "\n")
    write_text(os.path.join(texts, "methods.txt"), "\n".join(f"# {t}\n\n{x}\n" for t, x in methods))
    write_text(os.path.join(texts, "availability.txt"), "\n".join(f"# {t}\n\n{x}\n" for t, x in availability))
    r["state"] = "cut"
    save_papers(lib, rows)
    named_code = repositories("\n".join(x for _, x in availability) or raw)
    return (f"{key}: results {sum(len(x) for _, x in results):,} characters in {len(results)} sections, "
            f"{len(legends)} legends; methods {sum(len(x) for _, x in methods):,} characters; in {texts}"
            + (f"; code named: {', '.join(named_code)}" if named_code else ""))


# -- the module list --------------------------------------------------------------------------------------------------

MODULE_LIST = "module_list.md"
MODULE_LIST_INTRO = """# The modules a move can map to

Each module answers one question of a Perturb-seq screen. A move of a paper corresponds to a module when the module
asks the same question of the same kind of data. Below, from the knowledge base's module entries: each module's name,
what it is for, what it returns, and its choices where the entry gives them.
"""
MODULE_HEADING = re.compile(r"^## (\S+)\s*$", re.M)


def bold_paragraph(text, names):
    """The paragraph that opens with one of the bold names ("**The biology.** ..."), without the name; "" if none."""
    for name in names:
        m = re.search(rf"^\*\*{re.escape(name)}\.?\*\*\.?[ \t]*(.*?)(?:\n[ \t]*\n|\Z)", text, re.M | re.S | re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def section_text(text, names):
    """The text of a "## <name>" section up to the next heading, its list items joined by "; "; "" if none."""
    for name in names:
        m = re.search(rf"^##[ \t]+{re.escape(name)}[ \t]*\n(.*?)(?=^#{{1,3}}[ \t]|\Z)", text, re.M | re.S | re.I)
        if m:
            items = [re.sub(r"^\s*[-*]\s+", "", line).strip() for line in m.group(1).split("\n") if line.strip()]
            return "; ".join(items)
    return ""


def module_entry(path):
    """A knowledge base module entry's name (its heading's, else its file's), title, what it is for (the biology),
    what it returns (the measurement), and its choices where the entry gives them in a part of their own."""
    text = read_text(path)
    head = re.match(r"#[ \t]+(\S+?)[ \t]*:[ \t]*(.*)", text.strip())
    name, title = (head.group(1), head.group(2).strip()) if head else (os.path.basename(path)[:-3], "")
    return {"name": name, "title": title, "for": bold_paragraph(text, ("The biology",)),
            "returns": bold_paragraph(text, ("The measurement",)),
            "choices": bold_paragraph(text, ("The choices", "Choices")) or section_text(text, ("The choices",
                                                                                                "Choices"))}


def write_module_list(lib, knowledge_base=None):
    """The module list the moves readers and verifiers get, from the knowledge base's modules/*.md entries, written to
    the library; standard library only, so the plugin needs no Pertura installed."""
    kb = knowledge_base or settings(lib).get("knowledge_base")
    if not kb:
        raise LibraryError("no knowledge base was given at init; name its folder with --knowledge-base")
    folder = os.path.join(kb, "modules")
    if not os.path.isdir(folder):
        raise LibraryError(f"the knowledge base {kb} has no modules/ folder")
    entries = [module_entry(os.path.join(folder, f)) for f in sorted(os.listdir(folder)) if f.endswith(".md")]
    if not entries:
        raise LibraryError(f"{folder} holds no module entries")
    names = [e["name"] for e in entries]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise LibraryError(f"two entries give the name {', '.join(twice)}")
    lines = [MODULE_LIST_INTRO]
    for e in entries:
        lines += [f"## {e['name']}", "", f"- what it is for: {e['for'] or e['title'] or 'not given in the entry'}",
                  f"- what it returns: {e['returns'] or 'not given in the entry'}"]
        lines += [f"- choices: {e['choices']}"] if e["choices"] else []
        lines.append("")
    text = "\n".join(lines)
    path = os.path.join(lib, MODULE_LIST)
    before = hashlib.sha256(read_text(path).encode("utf-8")).hexdigest() if os.path.exists(path) else None
    write_text(path, text)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (f"{len(entries)} modules in {path}; sha256 {sha}" +
            ("; the list changed: rows read with the earlier list may name modules it no longer has"
             if before and before != sha else ""))


def module_names(lib):
    """The modules of the library's module list, the names a moves row may give."""
    path = os.path.join(lib, MODULE_LIST)
    if not os.path.exists(path):
        raise LibraryError(f"no module list in {lib}; write it with: papers.py modules {lib}")
    return MODULE_HEADING.findall(read_text(path))


# -- checking ---------------------------------------------------------------------------------------------------------

def normal(s):
    """A text as the checks compare it: one kind of dash, space and quote, a power of ten as e-notation, no reference
    numbers in brackets, lower case."""
    s = s.translate({0x2212: "-", 0x2013: "-", 0x2014: "-", 0x00a0: " ", 0x2009: " ", 0x202f: " ", 0x2010: "-",
                     0x2011: "-", 0x2018: "'", 0x2019: "'", 0x201a: "'", 0x201b: "'", 0x201c: '"', 0x201d: '"',
                     0x201e: '"', 0x2032: "'"})
    s = re.sub(r"(\d(?:\.\d+)?)\s*[×x]\s*10\s*-\s*(\d+)", r"\1e-\2", s)
    s = re.sub(r"\s*\[\d+(?:\s*[,-]\s*\d+)*\]", "", s)
    return re.sub(r"\s+", " ", s).lower()


TEXT_FILES = ("results.txt", "methods.txt", "availability.txt")
FILE_BREAK = "\0"  # between a paper's cut files in its text, so that no quote is found across two of them


def source_text(lib, key):
    """A paper's cut files as the checks compare them: the Results file first, then the Methods and the rest, each
    apart from the next."""
    folder = os.path.join(lib, "texts", key)
    if not os.path.isdir(folder):
        raise LibraryError(f"{key} has no cut text; cut it first")
    names = [f for f in os.listdir(folder) if f.endswith(".txt")]
    names = [f for f in TEXT_FILES if f in names] + sorted(f for f in names if f not in TEXT_FILES)
    return normal(FILE_BREAK.join(read_text(os.path.join(folder, f)) for f in names))


def table_rows(text):
    """The table's rows as lists of cells; a cell's own "|" is written "\\|"."""
    rows = []
    for line in text.split("\n"):
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")) or re.fullmatch(r"[|\s:-]+", s):
            continue
        rows.append([c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s[1:-1])])
    return rows


FIELD = re.compile(r"\s*(?:[-*]\s+)?(?:\*\*)?(paper|question|design|depth|repository|commit)(?:\*\*)?\s*:"
                   r"(?:\*\*)?\s*(.*)", re.I)


def field_lines(text):
    """{name: text} of the fields outside the table ("- **design:** ..." or "design: ..."), a field running on over
    the lines that follow it until a blank line, a table row or the next field."""
    out, name = {}, None
    for line in text.split("\n"):
        m = FIELD.fullmatch(line)
        if m:
            name = m.group(1).lower()
            out[name] = m.group(2).strip()
        elif name and line.strip() and not line.strip().startswith("|"):
            out[name] += " " + line.strip()
        else:
            name = None
    return out


def label_of(value):
    return "other" if value.startswith("other (") or value == "other" else value


def chart_as_listed(value):
    """A figures row's chart without the suffix an inferred chart carries, " (inferred)"."""
    return value[:-len(INFERRED)] if value.endswith(INFERRED) else value


def is_inferred(chart):
    return chart_as_listed(chart) != chart


def figures(panel):
    """The figures a panel names, as (main | supp | ext, number); a piece after a figure's name counts as one too.
    Extended Data figures are also written "ED Fig. 3" and "Fig. ED3", and an Expanded View figure ("Figure EV3") is
    read as one; an eLife figure supplement ("Figure 1—figure supplement 1") is none of the three, as a table is."""
    out, seen = [], False
    for piece in re.split(r"[,;]|\band\b", panel.lower()):
        p = piece.strip()
        if not p or "table" in p or re.search(r"figure\s+supplement", p):
            continue
        seen = seen or bool(re.search(r"\bfig", p))
        if not seen:
            continue
        m = re.search(r"(?:fig(?:ure)?s?\.?\s*)?(s|e[dv])?(\d+)", p)
        if m:
            kind = ("ext" if m.group(1) in ("ed", "ev") or
                    re.search(r"\bext(ended|\.)?\s*data|\bexpanded\s+view|\be[dv]\s*fig", p) else
                    "supp" if m.group(1) or "supp" in p else "main")
            out.append((kind, int(m.group(2))))
    return out


def figure_in(kind, n, text):
    if kind == "main":
        pat = (rf"(?<!supplementary )(?<!extended data )(?<!expanded view )(?<!supp\. )(?<!\bed )(?<!\bev )"
               rf"\bfig(?:ure)?s?\.?\s*{n}(?!\d)")
    elif kind == "supp":
        pat = rf"\bfig(?:ure)?s?\.?\s*s{n}(?!\d)|supplementary\s+fig(?:ure)?s?\.?\s*{n}(?!\d)"
    else:
        pat = (rf"(?:extended\s+data|expanded\s+view|\be[dv])\s+fig(?:ure)?s?\.?\s*{n}(?!\d)|"
               rf"\bfig(?:ure)?s?\.?\s*e[dv]\s?{n}(?!\d)")
    return re.search(pat, text) is not None


def numbers(value):
    """The numbers a field gives, but single digits, which every text holds."""
    found = re.findall(r"(?<![\w.])-?\d+(?:[.,]\d+)*(?:e-?\d+)?", normal(value))
    return [t for t in found if not re.fullmatch(r"\d", t)]


def number_in(t, text):
    forms = {t, t.replace(",", ""), t.lstrip("-")}
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?!\d)", text) for f in forms)


def sections_of(value):
    """The section headings a methods row names: several split at ";", without a "Results:" prefix or a figure in
    parentheses."""
    parts = []
    for part in value.split(";"):
        part = re.sub(r"\s*\([^)]*\)\s*$", "", re.sub(r"(?i)^\s*(results|methods)\s*:\s*", "", part)).strip()
        if part and part.lower() != "not stated":
            parts.append(part)
    return parts


def reason_in(reason, text):
    """Whether a reason is the paper's: "not stated", a quote found in the text, or words mostly the text's."""
    r = reason.strip()
    if not r or r.lower().startswith("not stated"):
        return ""
    quotes = re.findall(r"[\"“]([^\"“”]{4,})[\"”]", r)
    if quotes:
        missing = [q for q in quotes if normal(q) not in text]
        return f"quote not in the text: {missing}" if missing else ""
    words = [w for w in re.findall(r"[a-z][a-z-]{3,}", r.lower()) if w not in STOP]
    share = sum(w in text for w in words) / len(words) if words else 1
    return "" if share >= 0.6 else f"the reason's words are mostly not in the text ({share:.0%})"


def check_figures(panel, text, fails, looks):
    named = figures(panel)
    if not named:
        looks.append(f"the panel names no figure: {panel!r}")
        return
    found = [f for f in named if figure_in(*f, text)]
    if not found:
        fails.append(f"no figure of {panel!r} is in the text")
    elif len(found) < len(named):
        looks.append(f"some figures of {panel!r} are not in the text")


def check_numbers(value, text, looks, what):
    missing = [t for t in numbers(value) if not number_in(t, text)]
    if missing:
        looks.append(f"numbers of {what} not in the text: {', '.join(missing)}")


def quote_fails(name, quote, text, words=QUOTE_WORDS):
    """Why a quote fails: 8 to 40 words (or as many as words gives), no ellipsis, and found in the text after the
    checks' normalisation."""
    n = len(quote.split())
    if not words[0] <= n <= words[1]:
        return [f"{name} has {n} words, not {words[0]} to {words[1]}"]
    if "..." in quote or "…" in quote:
        return [f"{name} holds an ellipsis"]
    if normal(quote) not in text:
        return [f"{name} is not in the text"]
    return []


def check_move(fields, text, modules, fails):
    """The checks a moves row of the current version adds to the first version's: the unit in its list; the module
    in the module list or none; the relation in its list, and none exactly when the module is none; two quotes."""
    if fields["unit"] not in UNITS:
        fails.append(f"unit {fields['unit']!r} is not one of the list")
    if fields["module"] != "none" and fields["module"] not in modules:
        fails.append(f"module {fields['module']!r} is not in the module list")
    if fields["relation"] not in RELATIONS:
        fails.append(f"relation {fields['relation']!r} is not one of the list")
    elif (fields["module"] == "none") != (fields["relation"] == "none"):
        fails.append(f"the relation is none exactly when the module is: here module {fields['module']!r}, "
                     f"relation {fields['relation']!r}")
    for name in QUOTES:
        fails += quote_fails(name, fields[name], text)


def check_rows(reading, text, label, rows, kept, modules=()):
    """[(row, fails, looks)] for one reading's rows of one paper; modules are the names a moves row may give."""
    out = []
    for row in rows:
        fails, looks = [], []
        names = FIELDS[reading]
        if reading == "moves" and len(row) == len(MOVES_FIRST_VERSION):
            names = MOVES_FIRST_VERSION
        fields = dict(zip(names, row))
        if len(row) != len(names):
            fails.append(f"{len(row)} fields, not {len(names)}")
        elif reading != "chain" and fields["paper"] != label:
            fails.append(f"the paper is {fields['paper']!r}, not {label!r}")
        if len(row) == len(names):
            if reading == "moves":
                if label_of(fields["kind"]) not in KINDS:
                    fails.append(f"kind {fields['kind']!r} is not one of the list")
                check_figures(fields["panel"], text, fails, looks)
                check_numbers(fields["showed"], text, looks, "showed")
                why = reason_in(fields["decided_by"], text)
                looks += [f"decided_by: {why}"] if why else []
                if names is FIELDS["moves"]:
                    check_move(fields, text, modules, fails)
            elif reading == "chain":
                if not fields["step"].isdigit():
                    fails.append(f"step {fields['step']!r} is not a number")
                if fields["data"] not in DATA:
                    fails.append(f"data {fields['data']!r} is not one of the list")
                if fields["composed"] not in ("", "composed"):
                    fails.append(f"composed is {fields['composed']!r}, not 'composed' or empty")
                check_figures(fields["figure"], text, fails, looks)
                check_numbers(fields["showed"], text, looks, "showed")
            elif reading == "methods":
                if label_of(fields["step"]) not in STEPS:
                    fails.append(f"step {fields['step']!r} is not one of the list")
                if fields["as"] not in AS:
                    fails.append(f"as is {fields['as']!r}, not 'used' or 'also tried'")
                missing = [part for part in sections_of(fields["section"]) if normal(part) not in text]
                if missing:
                    looks.append(f"sections not in the text: {'; '.join(missing)}")
                check_numbers(fields["parameters"], text, looks, "parameters")
                why = reason_in(fields["reason"], text)
                looks += [f"reason: {why}"] if why else []
            elif label_of(fields["step"]) not in STEPS:
                fails.append(f"step {fields['step']!r} is not one of the list")
        cited = kept.cited_in(" | ".join(row))
        if cited:
            fails.append(f"cites sources kept out: {', '.join(cited)}")
        out.append((row, fails, looks))
    return out


def code_line(root, file, line, after=0):
    """The text at a file's line (and the lines after it, for reading), with a notebook's JSON escapes undone; None
    if it is not there."""
    path = os.path.normpath(os.path.join(root, *file.split("/")))
    if not path.startswith(os.path.normpath(root) + os.sep) or not os.path.isfile(path) or not line.isdigit():
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    n = int(line)
    if not 1 <= n <= len(lines):
        return None
    text = " ".join(lines[n - 1:n + after])
    if file.lower().endswith(".ipynb"):
        text = text.replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\")
    return text


def check_code(lib, key, r, text, rows, kept):
    head = field_lines(text)
    out = check_rows("code", "", r["label"], rows, kept)
    root = code_root(lib, key)
    whole = []
    if head.get("repository") != r["code_url"] or head.get("commit") != r["code_commit"]:
        whole.append(f"the repository and commit are not the ones fetched ({r['code_url']} at {r['code_commit']})")
    for row, fails, _ in out:
        if len(row) != len(FIELDS["code"]):
            continue
        fields = dict(zip(FIELDS["code"], row))
        at = code_line(root, fields["file"], fields["line"]) if root else None
        if at is None:
            fails.append(f"{fields['file']}:{fields['line']} is not in the repository")
        elif re.sub(r"\s+", "", fields["call"].strip("`")) not in re.sub(r"\s+", "", at):
            fails.append(f"the call is not at {fields['file']}:{fields['line']}")
    return out, whole


FIRST_VERSION_ROW = ("a row of the moves reading's first version ({} fields, not {}); read the paper again with the "
                     "current brief")


def check_moves(lib, label, text, rows, kept):
    """[(row, fails, looks)] of moves rows held to the current version: a first-version row fails."""
    checked = check_rows("moves", text, label, rows, kept, module_names(lib))
    for row, fails, _ in checked:
        if len(row) == len(MOVES_FIRST_VERSION):
            fails.insert(0, FIRST_VERSION_ROW.format(len(row), len(FIELDS["moves"])))
    return checked


def design_of(path, text):
    """({feature: value}, fails) of a design.md: the six features, each value from its list, each evidence a span
    of the text or "not stated"."""
    out, fails = {}, []
    if not os.path.exists(path):
        return out, ["no design file"]
    for row in table_rows(read_text(path)):
        if len(row) != 3:
            fails.append(f"a design line with {len(row)} fields, not 3")
            continue
        feature, value, evidence = row
        if feature not in DESIGN:
            fails.append(f"no feature {feature!r} in the list")
            continue
        if value not in DESIGN[feature]:
            fails.append(f"{feature}: {value!r} is not one of its list")
        if evidence.lower() != "not stated" and normal(evidence) not in text:
            fails.append(f"{feature}: the evidence is not in the text")
        out[feature] = value
    fails += [f"no line for {f}" for f in DESIGN if f not in out]
    return out, fails


def pipeline_folder(lib, key, p):
    return os.path.join(lib, "rows", key, f"pipe_{p}")


def figures_folder(lib, key, n):
    return os.path.join(lib, "rows", key, f"figures_{n}")


def rows_file(lib, key, reading, pipeline=None):
    """The file of a reading's rows: for the moves reading by pipelines, the pipeline's verified rows; for the figures
    reading, reading n's rows (n given as pipeline)."""
    if reading == "moves" and pipeline:
        return os.path.join(pipeline_folder(lib, key, pipeline), "verified.md")
    if reading == "figures" and pipeline:
        return os.path.join(figures_folder(lib, key, pipeline), "figures.md")
    return os.path.join(lib, "rows", key, f"{reading}.md")


def text_sha1(path):
    """The sha1 of a file's text as the checks read it, or None where there is no file."""
    return hashlib.sha1(read_text(path).encode("utf-8")).hexdigest() if os.path.exists(path) else None


def files_read(lib, paths):
    """{path in the library: sha1 of its text} of the files a command read, so that a page built later from them can
    tell whether they changed."""
    return {os.path.relpath(p, lib).replace(os.sep, "/"): text_sha1(p) for p in paths}


def checked_file(lib, key, reading):
    """The file a reading's rows were checked in, as the paper's check records it."""
    path = os.path.join(lib, "checks", f"{key}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            rep = json.load(fh).get(reading, {})
        if rep.get("file"):
            return os.path.join(lib, *rep["file"].split("/"))
    return rows_file(lib, key, reading)


def version_beside(path):
    """Where a reading's rows file has the version its reading was started under: a pipeline's or a figures reading's
    folder holds version.json; a reading of one file, rows/<key>/<reading>.md, has <reading>.version.json beside it."""
    folder = os.path.dirname(path)
    if re.fullmatch(r"(?:pipe|figures)_\d+", os.path.basename(folder)):
        return os.path.join(folder, VERSION_FILE)
    return path[:-len(".md")] + ".version.json"


def version_of(reading):
    """The reading as its briefs and lists stand: each brief's sha256, the sha256 of the briefs and the lists together,
    and the fixed version that is, or None."""
    briefs = {}
    for name in BRIEFS[reading]:
        path = os.path.join(PLUGIN_FOLDER, "agents", name)
        if not os.path.exists(path):
            raise LibraryError(f"no brief at {path}")
        briefs[name] = hashlib.sha256(read_text(path).encode("utf-8")).hexdigest()
    whole = hashlib.sha256(json.dumps([briefs, LISTS[reading]]).encode("utf-8")).hexdigest()
    fixed = [name for name, sha in VERSIONS.get(reading, {}).items() if sha == whole]
    return {"reading": reading, "version": fixed[0] if fixed else None, "sha256": whole, "briefs": briefs}


def recorded_version(path):
    """The version recorded beside a reading's rows file when the reading was started, or None."""
    v = version_beside(path)
    if not os.path.exists(v):
        return None
    with open(v, encoding="utf-8") as fh:
        return json.load(fh)


def fixed_version(reading, recorded):
    """The fixed version a reading was started under, by its name and sha256 as VERSIONS gives them, or None."""
    name = (recorded or {}).get("version")
    return name if name and VERSIONS.get(reading, {}).get(name) == recorded.get("sha256") else None


def start(lib, key, reading, n=None):
    """One reading of a paper started, before its reader is launched: the version of the reading's briefs and lists
    recorded beside where its rows go. A reading that has fixed versions starts only under one; a reading whose rows
    are written already is not started again."""
    paper(papers(lib), key)
    if not os.path.isdir(os.path.join(lib, "texts", key)):
        raise LibraryError(f"{key} has no cut text; cut it first")
    if reading in ("moves", "figures") and not n:
        raise LibraryError("name the pipeline with --pipeline" if reading == "moves" else
                           "name the reading with --figures")
    path = rows_file(lib, key, reading, n)
    folder = os.path.dirname(path)
    if reading in ("moves", "figures"):
        written = sorted(f for f in os.listdir(folder) if f.endswith(".md")) if os.path.isdir(folder) else []
    else:
        written = [os.path.basename(path)] if os.path.exists(path) else []
    if written:
        raise LibraryError(f"{key}'s {reading} reading has rows already ({', '.join(written)} in {folder})")
    if os.path.exists(version_beside(path)):
        raise LibraryError(f"{key}'s {reading} reading was started already: {version_beside(path)}")
    v = version_of(reading)
    if VERSIONS.get(reading) and not v["version"]:
        raise LibraryError(f"the {reading} reading's briefs or lists are not one of its fixed versions "
                           f"({', '.join(VERSIONS[reading])}): record the new version before reading")
    v.update(key=key, n=n, started=time.strftime("%Y-%m-%d"))
    write_text(version_beside(path), json.dumps(v, indent=1) + "\n")
    return [f"{key} {reading}" + (f" {n}" if n else "") + ": started under " +
            (v["version"] or "no fixed version (the reading has none yet)") + f", sha256 {v['sha256'][:12]}; its "
            f"reader writes into {folder if reading == 'moves' else path}"]


def check(lib, key, pipeline=None, figure_reading=None):
    """Every reading's rows of one paper checked; the moves are pipeline P's verified rows when P is given, the
    figures reading N's rows when N is given."""
    rows = papers(lib)
    r = paper(rows, key)
    kept, folder = KeptOut.of(lib), os.path.join(lib, "rows", key)
    text = source_text(lib, key)
    report, lines = {}, []
    if pipeline and not os.path.exists(rows_file(lib, key, "moves", pipeline)):
        raise LibraryError(f"no verified rows at {rows_file(lib, key, 'moves', pipeline)}")
    if figure_reading and not os.path.exists(rows_file(lib, key, "figures", figure_reading)):
        raise LibraryError(f"no rows at {rows_file(lib, key, 'figures', figure_reading)}")
    entries = os.listdir(folder) if os.path.isdir(folder) else []
    pipes = sorted(d for d in entries if re.fullmatch(r"pipe_\d+", d))
    if pipes and not pipeline and not os.path.exists(rows_file(lib, key, "moves")):
        lines.append(f"moves: read by {', '.join(pipes)}; name the pipeline whose verified rows are the paper's with "
                     "--pipeline")
    readers = sorted(d for d in entries if re.fullmatch(r"figures_\d+", d))
    if readers and not figure_reading and not os.path.exists(rows_file(lib, key, "figures")):
        lines.append(f"figures: read by {', '.join(readers)}; name the reading whose rows are the paper's with "
                     "--figures")
    for reading in READINGS:
        path = rows_file(lib, key, reading, figure_reading if reading == "figures" else pipeline)
        if not os.path.exists(path):
            continue
        written = read_text(path)
        whole = []
        if reading == "code":
            checked, whole = check_code(lib, key, r, written, table_rows(written), kept)
        elif reading == "moves" and not os.path.exists(os.path.join(lib, MODULE_LIST)):
            # the moves are checked against the module list: without one they fail, and the paper's other readings
            # are checked as usual
            why = f"no module list in the library; write it with: papers.py modules {lib}"
            checked = [(row, [why], []) for row in table_rows(written)]
        elif reading == "moves":
            checked = check_moves(lib, r["label"], text, table_rows(written), kept)
        elif reading == "figures":
            legs = paper_legends(lib, key)
            figs = check_figures_reading(table_rows(written), r["label"], text, legs, kept,
                                         images=paper_images(lib, key), pdf=paper_pdf(lib, key),
                                         version=fixed_version(reading, recorded_version(path)))
            checked = figs["rows"]
        else:
            checked = check_rows(reading, text, r["label"], table_rows(written), kept)
        if reading == "chain":
            head = field_lines(written)
            whole += [f"no {f} field" for f in CHAIN_FIELDS if not head.get(f)]
            if head.get("paper") and head["paper"] != r["label"]:
                whole.append(f"the paper is {head['paper']!r}, not {r['label']!r}")
            steps = [row[0] for row, _, _ in checked]
            if steps != [str(i + 1) for i in range(len(steps))]:
                whole.append("the steps are not numbered 1, 2, 3 in order")
        if not checked:
            whole.append("no rows")
        report[reading] = {"sha1": hashlib.sha1(written.encode("utf-8")).hexdigest(), "whole": whole,
                           "file": os.path.relpath(path, lib).replace(os.sep, "/"),
                           "version": recorded_version(path),
                           "rows": [{"row": row, "fails": f, "looks": lk} for row, f, lk in checked]}
        failed = sum(bool(f) for _, f, _ in checked)
        looked = sum(bool(lk) for _, _, lk in checked)
        where = (f" of {report[reading]['file']}" if (reading == "moves" and pipeline) or
                 (reading == "figures" and figure_reading) else "")
        lines.append(f"{reading}: {len(checked)} rows{where}; {failed} fail, {looked} to look at" +
                     (f"; {'; '.join(whole)}" if whole else ""))
        if VERSIONS.get(reading):
            fixed = fixed_version(reading, report[reading]["version"])
            lines.append(f"  read under {fixed}" if fixed else
                         f"  not read under a fixed version of the reading ({', '.join(VERSIONS[reading])}), so it "
                         "is not merged: a reading is started with `papers.py start` before its reader is launched")
        for i, (row, f, lk) in enumerate(checked, 1):
            for why in f:
                lines.append(f"  row {i} fails: {why}")
            for why in lk:
                lines.append(f"  row {i}, to look at: {why}")
        if reading == "figures":
            conclusions = {figure_name(fig): v for fig, v in figs["conclusions"].items()}
            report[reading].update(conclusions=conclusions, counts=figs["counts"],
                                   units_not_read=figs["units_not_read"])
            c = figs["counts"]
            lines.append(f"conclusions: {c['figures_with_one_conclusion_found']} of {c['figures_read']} figures have "
                         "one, on their first row, found in the text; the rows that pass cover "
                         f"{c['legend_units_covered']} of the legends' {c['legend_units']} panels")
            for name, v in conclusions.items():
                for why in v["fails"]:
                    lines.append(f"  {name}, its conclusion fails: {why}")
        if reading == "moves":
            if os.path.exists(os.path.join(lib, MODULE_LIST)):
                module_list = read_text(os.path.join(lib, MODULE_LIST))
                report[reading]["module_list_sha256"] = hashlib.sha256(module_list.encode("utf-8")).hexdigest()
            design, design_fails = design_of(os.path.join(os.path.dirname(path), "design.md"), text)
            report[reading]["design"] = {"values": design, "fails": design_fails}
            lines.append(f"design: {len(design)} of {len(DESIGN)} features" +
                         (f"; {'; '.join(design_fails)}" if design_fails else ""))
    if not report:
        raise LibraryError(f"{key} has no rows in {folder}" + (f"; {lines[0]}" if lines else ""))
    write_text(os.path.join(lib, "checks", f"{key}.json"), json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    r["state"] = "checked"
    save_papers(lib, rows)
    return lines


def sentences_with(text, panel, limit=3):
    """The sentences of a text that name one of a panel's figures, those that also name its first panel's letter
    first."""
    named = figures(panel)
    m = re.search(r"(\d+)\s*([a-z])", panel.lower())
    letter = None
    if m:
        n, a = m.groups()
        letter = re.compile(rf"\b{n}\s*{a}\b|\b{n}[a-z]?\s*[-,]\s*{a}\b")
    hits = []
    for i, s in enumerate(re.split(r"(?<=[.!?])\s+(?=[A-Z])", re.sub(r"\s+", " ", text))):
        low = normal(s)
        if any(figure_in(k, n, low) for k, n in named):
            hits.append((0 if letter and letter.search(low) else 1, i, s[:400]))
    return [s for _, _, s in sorted(hits)[:limit]]


def show(lib, key, n=2):
    r = paper(papers(lib), key)
    folder = os.path.join(lib, "texts", key)
    text = " ".join(read_text(os.path.join(folder, f)) for f in ("results.txt", "methods.txt")
                    if os.path.exists(os.path.join(folder, f)))
    out = []
    for reading in READINGS:
        path = checked_file(lib, key, reading)
        if not os.path.exists(path):
            continue
        rows = table_rows(read_text(path))
        picked = sorted({round(i * (len(rows) - 1) / max(n - 1, 1)) for i in range(min(n, len(rows)))})
        for i in picked:
            fields = dict(zip(figure_names(rows[i]) if reading == "figures" else FIELDS[reading], rows[i]))
            out.append(f"\n{reading}, row {i + 1} of {len(rows)}:")
            out += [f"  {k}: {v}" for k, v in fields.items()]
            if reading in ("moves", "chain", "figures"):
                place = (figure_place(fields) if reading == "figures" else
                         fields.get("panel") or fields.get("figure", ""))
                out.append("  the text:")
                out += [f"    {s}" for s in sentences_with(text, place)]
            elif reading == "methods":
                at = next((i for i in (text.find(p) for p in sections_of(fields.get("section", ""))) if i >= 0), -1)
                out.append(f"  the text: {text[at:at + 800]}" if at >= 0 else "  the text: the section was not found")
            else:
                at = code_line(code_root(lib, key), fields.get("file", ""), fields.get("line", ""), after=3)
                out.append(f"  the code: {at}")
    return [f"{key} ({r['label']})"] + out


# -- two pipelines ----------------------------------------------------------------------------------------------------

LETTERS = r"[a-z](?:\s*[-–,]\s*[a-z](?![a-z]))*"
PANEL_FIGURE = re.compile(r"(extended\s+data\s+fig\w*\.?|ed\s+fig\w*\.?|supplementary\s+fig\w*\.?|supp\.?\s+fig\w*\.?|"
                          r"fig\w*\.?)\s*(s?)(\d+)\s*(" + LETTERS + r")?(?![a-z0-9])")
PANEL_MORE = re.compile(r"^\s*(?:,|and|&)\s*(s?)(\d+)\s*(" + LETTERS + r")?(?![a-z0-9])")
# "Extended Data 2a-d" and "ED 2a" name a figure as "Extended Data Fig. 2a-d" does
EXTENDED_DATA = re.compile(r"\b(extended\s+data|ed)\s+(?=s?\d)", re.I)
MATCH_STOP = set("""with from that this these those were was have has had into onto over under than then their there
which what when where while whose also each other such more most many much some only both same between within across
against after before during upon about using used cells cell genes gene data analysis analyses screen screens
perturbation perturbations perturbed guide guides target targets""".split())


def panel_letters(spec):
    """'c-e' -> c, d, e; 'a,b' -> a, b; '' -> '*' (the whole figure)."""
    if not spec:
        return ["*"]
    out = []
    for part in re.split(r"\s*,\s*", spec.strip()):
        m = re.fullmatch(r"([a-z])\s*[-–]\s*([a-z])", part)
        if m:
            a, b = ord(m.group(1)), ord(m.group(2))
            out += [chr(c) for c in range(min(a, b), max(a, b) + 1)]
        elif part:
            out.append(part)
    return out or ["*"]


def panel_tokens(panel):
    """{(main | supp | ext, figure, letter)} of a panel field; letter '*' for a figure named whole."""
    s = EXTENDED_DATA.sub("Extended Data Fig. ", panel).lower()
    out = set()
    for m in PANEL_FIGURE.finditer(s):
        word = m.group(1)
        kind = ("ext" if word.startswith(("extended", "ed")) else
                "supp" if m.group(2) or word.startswith(("supplementary", "supp")) else "main")
        figs = [(m.group(2), m.group(3), m.group(4))]
        rest = s[m.end():]
        while True:
            k = PANEL_MORE.match(rest)
            if not k:
                break
            figs.append((k.group(1), k.group(2), k.group(3)))
            rest = rest[k.end():]
        for sflag, num, letters in figs:
            out |= {("supp" if sflag else kind, int(num), x) for x in panel_letters(letters)}
    return out


def tokens_overlap(a, b):
    """Whether two panels share a token (a whole figure matching any letter of it), and the Jaccard index of the
    shared tokens."""
    def hits(x, y):
        return {t for t in x if t in y or (t[2] == "*" and any(u[:2] == t[:2] for u in y))
                or any(u[2] == "*" and u[:2] == t[:2] for u in y)}
    shared = hits(a, b) | hits(b, a)
    union = a | b
    return bool(shared), (len(shared) / len(union) if union else 0.0)


def content_words(text):
    return {w for w in re.findall(r"[a-z][a-z0-9-]{3,}", text.lower()) if w not in MATCH_STOP}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def match(readings):
    """Items from the checked rows of each pipeline, {pipeline: [row]}: [{pipeline: row index}], each item holding
    at most one row of each pipeline. Two rows are a candidate pair when their panels share a token and their content
    words (question, cells, compared_with, method, readout) have a Jaccard index of at least CONTENT_MIN; pairs join
    greedily, best first by the sum of the two indices. Neither the module nor the kind is used."""
    feats = {(p, i): (panel_tokens(row["panel"]), content_words(row["question"] + " " + row["analysis"]))
             for p, rows in readings.items() for i, row in enumerate(rows)}
    pairs = []
    keys = sorted(feats)
    for x in range(len(keys)):
        for y in range(x + 1, len(keys)):
            a, b = keys[x], keys[y]
            if a[0] == b[0]:
                continue
            share, pj = tokens_overlap(feats[a][0], feats[b][0])
            cj = jaccard(feats[a][1], feats[b][1])
            if share and cj >= CONTENT_MIN:
                pairs.append((pj + cj, a, b))
    item_of = {k: {k[0]: k[1]} for k in keys}
    for _, a, b in sorted(pairs, key=lambda t: (-t[0], t[1], t[2])):
        ia, ib = item_of[a], item_of[b]
        if ia is ib or set(ia) & set(ib):
            continue
        ia.update(ib)
        for p, i in ib.items():
            item_of[(p, i)] = ia
    seen, items = set(), []
    for k in keys:
        it = item_of[k]
        if id(it) not in seen:
            seen.add(id(it))
            items.append(dict(sorted(it.items())))
    return items


def agreement(readings):
    """(items, measures) of two pipelines' checked rows: the row agreement (the share of rows in an item both
    found), and among those items the share whose two rows give the same module, relation, both, and kind (as
    written, and with "other (...)" as one kind)."""
    items = match(readings)
    pairs = [it for it in items if len(it) == 2]

    def same(value):
        return round(sum(len({value(readings[p][i]) for p, i in it.items()}) == 1 for it in pairs) / max(1, len(pairs)),
                     3)
    n = sum(len(rows) for rows in readings.values())
    return items, {"items": len(items), "items_by_support": {s: sum(len(it) == s for it in items) for s in (1, 2)},
                   "row_agreement": round(sum(len(it) for it in pairs) / max(1, n), 3),
                   "module_agreement": same(lambda x: x["module"]),
                   "relation_agreement": same(lambda x: x["relation"]),
                   "module_and_relation_agreement": same(lambda x: (x["module"], x["relation"])),
                   "kind_agreement_as_written": same(lambda x: x["kind"]),
                   "kind_agreement": same(lambda x: label_of(x["kind"]))}


def pipeline_rows(lib, path, label, text, kept):
    """(the rows that pass their checks, as dicts, with their number in the file; the failures; the counts) of one
    pipeline's moves file. A row's quotes count as found only when the row has its fields and both quotes pass."""
    rows = table_rows(read_text(path)) if os.path.exists(path) else []
    good, failed, quotes = [], [], 0
    for i, (row, fails, _) in enumerate(check_moves(lib, label, text, rows, kept), 1):
        quotes += len(row) == len(FIELDS["moves"]) and not any(f.startswith(QUOTES) for f in fails)
        if fails:
            failed.append({"row": i, "panel": row[1] if len(row) > 1 else "", "fails": fails})
            continue
        d = dict(zip(FIELDS["moves"], row), row=i)
        d["analysis"] = " ".join(d[k] for k in ("cells", "compared_with", "method", "readout"))
        good.append(d)
    return good, failed, {"rows": len(rows), "passed": len(good), "quotes_found": quotes}


def verdicts_of(path):
    """([{row, verdict, changed}], the moves named as missed) of a verifier's verdicts.md."""
    if not os.path.exists(path):
        return [], []
    text = read_text(path)
    missed = [line.strip()[len("missed:"):].strip() for line in text.split("\n")
              if line.strip().lower().startswith("missed:")]
    lines = [{"row": row[0], "verdict": row[1].lower() if len(row) > 1 else "",
              "changed": row[2] if len(row) > 2 else ""} for row in table_rows(text)]
    return lines, missed


def review_list(final, items, pipes):
    """The moves one pipeline found and the other did not, the moves whose modules differ, the rows that failed
    their checks, and the moves the verifiers name as missed."""
    lines = []
    for it in items:
        modules = {final[p][i]["module"] for p, i in it.items()}
        if len(it) == 1 or len(modules) > 1:
            why = "one pipeline" if len(it) == 1 else "modules differ: " + "; ".join(sorted(modules))
            where = ", ".join(f"pipe {p} row {final[p][i]['row']}" for p, i in it.items())
            p, i = next(iter(it.items()))
            lines.append(f"{why} | {where} | {final[p][i]['panel']} | {final[p][i]['question'][:120]}")
    for p, pr in pipes.items():
        lines += [f"failed check | pipe {p} row {f['row']} | {f['panel']} | {'; '.join(f['fails'])}"
                  for f in pr["failed"]]
        lines += [f"verifier {p} names a missed move | {x}" for x in pr["missed"]]
    return lines


def moves_items(final, items):
    """The moves matched from two pipelines' verified rows as the agreement file keeps them: each one's support, its
    rows, and the modules, relations, kinds and panels they give."""
    return [{"support": len(it), "rows": {str(p): final[p][i]["row"] for p, i in it.items()},
             "modules": sorted({final[p][i]["module"] for p, i in it.items()}),
             "relations": sorted({final[p][i]["relation"] for p, i in it.items()}),
             "kinds": sorted({final[p][i]["kind"] for p, i in it.items()}),
             "panels": sorted({final[p][i]["panel"] for p, i in it.items()})} for it in items]


def agree(lib, key):
    """Two pipelines' readings of one paper: every row of each checked, the verified rows matched into items, the
    measures against the pass line, the review list; written to checks/<key>.agreement.json."""
    r = paper(papers(lib), key)
    text, kept = source_text(lib, key), KeptOut.of(lib)
    final, before, pipes, quotes = {}, {}, {}, [0, 0]
    for p in PIPELINES:
        base = pipeline_folder(lib, key, p)
        if not os.path.exists(os.path.join(base, "verified.md")):
            raise LibraryError(f"no verified rows at {os.path.join(base, 'verified.md')}; both pipelines' verifiers "
                               "write theirs first")
        final[p], failed, n = pipeline_rows(lib, os.path.join(base, "verified.md"), r["label"], text, kept)
        before[p], before_failed, rn = pipeline_rows(lib, os.path.join(base, "moves.md"), r["label"], text, kept)
        quotes[0] += n["quotes_found"]
        quotes[1] += n["rows"]
        verdicts, missed = verdicts_of(os.path.join(base, "verdicts.md"))
        counts = {v: sum(x["verdict"] == v for x in verdicts) for v in VERDICTS}
        notes = []
        odd = [x["verdict"] for x in verdicts if x["verdict"] not in VERDICTS]
        if odd:
            notes.append(f"verdicts not in the list: {', '.join(odd)}")
        if len(verdicts) != rn["rows"]:
            notes.append(f"{len(verdicts)} verdicts for the reader's {rn['rows']} rows")
        if counts["accept"] + counts["fix"] != n["rows"]:
            notes.append(f"{counts['accept'] + counts['fix']} rows accepted or fixed, {n['rows']} verified rows")
        design, design_fails = design_of(os.path.join(base, "design.md"), text)
        pipes[p] = {"reader": rn, "verified": n, "failed": failed, "reader_failed": before_failed, "verdicts": counts,
                    "verdict_notes": notes, "missed": missed, "design": design, "design_fails": design_fails}
    items, m = agreement(final)
    m["before_verification"] = agreement(before)[1]
    m["quote_check"] = round(quotes[0] / max(1, quotes[1]), 3)
    m["pass"] = {name: m[name] >= line for name, line in PASS.items()}
    m["passes"] = all(m["pass"].values())
    features = {f: [pipes[p]["design"].get(f) for p in PIPELINES] for f in DESIGN}
    m["design_agreement"] = sum(len(set(v)) == 1 and v[0] is not None for v in features.values())
    m["design"] = features
    review = review_list(final, items, pipes)
    module_list = read_text(os.path.join(lib, MODULE_LIST))
    read = [os.path.join(pipeline_folder(lib, key, p), name) for p in PIPELINES
            for name in ("moves.md", "verdicts.md", "verified.md", "design.md")]
    report = {"key": key, "label": r["label"],
              "module_list_sha256": hashlib.sha256(module_list.encode("utf-8")).hexdigest(),
              "read": files_read(lib, read), "measures": m, "pipelines": pipes, "review": review,
              "items": moves_items(final, items)}
    path = os.path.join(lib, "checks", f"{key}.agreement.json")
    write_text(path, json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    out = [f"{key} ({r['label']}): two pipelines"]
    for p, pr in pipes.items():
        v = pr["verdicts"]
        out.append(f"  pipeline {p}: the reader's {pr['reader']['rows']} rows, {pr['reader']['passed']} pass their "
                   f"checks; the verifier accepts {v['accept']}, fixes {v['fix']}, deletes {v['delete']}; "
                   f"{pr['verified']['rows']} verified rows, {pr['verified']['passed']} pass their checks; "
                   f"{len(pr['missed'])} moves named as missed" +
                   (f"; {'; '.join(pr['verdict_notes'])}" if pr["verdict_notes"] else ""))
    for name, line in PASS.items():
        out.append(f"  {name.replace('_', ' ')} {m[name]:.2f} (line {line:.2f}): "
                   + ("passes" if m["pass"][name] else "does not pass"))
    b = m["before_verification"]
    out += [f"  reported: relation agreement {m['relation_agreement']:.2f}; module and relation "
            f"{m['module_and_relation_agreement']:.2f}; kind {m['kind_agreement']:.2f} with other (...) as one kind, "
            f"{m['kind_agreement_as_written']:.2f} as written",
            f"  reported, the readers' rows before verification: row {b['row_agreement']:.2f}; module "
            f"{b['module_agreement']:.2f}; kind {b['kind_agreement']:.2f}",
            f"  design features the two readers agree on: {m['design_agreement']} of {len(DESIGN)}",
            "  the paper passes" if m["passes"] else
            f"  the paper does not pass: {', '.join(k.replace('_', ' ') for k, ok in m['pass'].items() if not ok)}"]
    out += ["review list:"] + [f"  - {x}" for x in review] if review else ["review list: empty"]
    return out + [f"written to {path}"]


# -- the figures reading ----------------------------------------------------------------------------------------------

LEGENDS_HEADING = "# figure and supplementary legends"  # where the cut Results file's legends begin, in lower case
DASH = "-‐‑‒–—−"
LETTER_SEP = rf"\s*(?:,\s*(?:and\b|&)?|&|\band\b|\bto\b|[{DASH}])\s*"
# "(A)", "(B and C)", "(B, C, and D)", "(F-G)", "(B&C)", "(d)"; not one glued to a word, as in "guide(s)" or "f(X)"
MARKER_PAREN = re.compile(rf"(?<![\w(])\(([A-Za-z](?:{LETTER_SEP}[A-Za-z])*)\)")
MARKER_PAREN_RANGE = re.compile(rf"(?<![\w(])\(([A-Za-z])\)\s*[{DASH}]\s*\(([A-Za-z])\)")
MARKER_BARE = re.compile(rf"(?:^|(?<=[\s.;]))([a-z](?:{LETTER_SEP}[a-z])*)\)")  # "a) ...", "c-e) ..."
# "a, Schematic ...", "b-d, UMAP ...", as some journals mark panels: only where a legend's text or a sentence begins
MARKER_COMMA = re.compile(rf"(?:^|(?<=[.;:!?])\s+)([A-Za-z](?![A-Za-z])(?:{LETTER_SEP}[A-Za-z](?![A-Za-z]))*),\s")
# "a. Normalized ...", "c–d. UMAP ...", as Nature's Extended Data legends mark panels: lower case only, after a space or
# where the legend's text begins, and a panel's only where it names the next letter or one already named, wherever
# it stands, so that "E. coli", "Dr. J. D. Smith", "i. e." or "n. s." add no panel, and "... correction d. Gene
# program" does
MARKER_PERIOD = re.compile(rf"(?:^|(?<=\s))([a-z](?![A-Za-z])(?:{LETTER_SEP}[a-z](?![A-Za-z]))*)\.(?=\s)")
# Bare labels precede a capitalized word or acronym ("A GWAS", "C eQTL", "a Workflow").
MARKER_SPACE = re.compile(rf"(?:^|(?<=[.;:!?])\s+)([A-Za-z](?![A-Za-z])"
                          rf"(?:{LETTER_SEP}[A-Za-z](?![A-Za-z]))*)\s+(?=[A-Z0-9]|[a-z]+[A-Z])")
# a caption's label: a figure in any form `figures` reads, an eLife figure supplement included, so that each caption
# is a legend of its own
LEGEND_START = re.compile(rf"^(?:-\s+)?((?:(?:extended\s+data|expanded\s+view|ed|ev|supplementary|supplemental)\s+)?"
                          rf"fig(?:ure)?s?\.?\s*(?:s|e[dv])?\s?\d+(?:\s*[{DASH}]\s*figure\s+supplement\s+\d+)?)", re.I)
LEGEND_OTHER = re.compile(r"^(?:-\s+)?(?:(?:supplementary|supplemental)\s+)?(?:doc|table|movie|video|data|appendix)\b",
                          re.I)


def legend_letters(group):
    """The letters a panel marker or a row's panel names, in lower case: "B and C", "F-G", "a, c"; None if a piece is
    neither a letter nor a range."""
    out = set()
    for piece in re.split(r"\s*(?:,|&|;|\band\b)\s*", group.strip()):
        piece = piece.strip().lower()
        if not piece:
            continue
        m = re.fullmatch(rf"([a-z])\s*(?:\bto\b|[{DASH}])\s*([a-z])", piece)
        if m and m.group(1) <= m.group(2):
            out |= {chr(c) for c in range(ord(m.group(1)), ord(m.group(2)) + 1)}
        elif re.fullmatch(r"[a-z]", piece):
            out.add(piece)
        else:
            return None
    return out


def panel_markers(body):
    """A legend's panel markers as (position, letters), in one case: the case of the marker that names its first panel,
    a (where a sentence begins, if one does there), else upper case where it has "(A)" markers. Markers are "(A)",
    "(c)", "a)", "a," where a sentence begins, and "a." in lower case after a space. A marker where the legend's text
    or a sentence begins is a panel's; one inside a sentence, and an "a." anywhere, only when it names a letter already
    named or the next one, so that "matrix (X)", "steps (i) to (iii)" or "i. e." add no panel. Bare labels need an
    a/b sequence in one case; parenthesized letters inside such captions are cross-references."""
    ranges = list(MARKER_PAREN_RANGE.finditer(body))
    found = [(m.start(), m.group(1)) for m in MARKER_PAREN.finditer(body)
             if not any(r.start() <= m.start() < r.end() for r in ranges)]
    found += [(m.start(), m.group(1) + "-" + m.group(2)) for m in ranges]
    found += [(m.start(1), m.group(1)) for m in MARKER_BARE.finditer(body)]
    found += [(m.start(1), m.group(1)) for m in MARKER_COMMA.finditer(body)]
    period = {(m.start(1), m.group(1)) for m in MARKER_PERIOD.finditer(body)}
    found = sorted(set(found) | period)

    plain = [(m.start(1), m.group(1)) for m in MARKER_SPACE.finditer(body)]
    runs = []
    for upper in (False, True):
        run = [(at, group) for at, group in plain if group[0].isupper() == upper]
        letters = set().union(*(legend_letters(group) or set() for _, group in run))
        if len(run) > 1 and {"a", "b"} <= letters:
            runs.append((len(letters), run))
    if runs:
        _, found = max(runs, key=lambda x: x[0])
        period = set(found)  # keep inline references out; bare markers must follow the panel sequence

    def begins(at):
        before = body[:at].rstrip()
        return not before or before[-1] in ".;:!?"
    firsts = [(not begins(at), at, group) for at, group in found if "a" in (legend_letters(group) or ())]
    if firsts:
        upper = min(firsts)[2][0].isupper()
    else:
        upper = any(group[0].isupper() for at, group in found if body[at] == "(")
    out, last = [], None
    for at, group in found:
        letters = legend_letters(group)
        if group[0].isupper() != upper or not letters:
            continue
        if (begins(at) and (at, group) not in period) or min(letters) <= (chr(ord(last) + 1) if last else "a"):
            out.append((at, group))
            last = max(letters | ({last} if last else set()))
    return out


def figure_legends(results):
    """{(main | supp | ext, number): {label, body, letters, title}} of the legends in a cut Results file: each figure's
    panel letters, from its legend's markers, and its title, the legend's text before its first marker. Each caption
    is a legend of its own; a figure's first legend is kept, so that a later caption naming it (an eLife figure's
    source data) does not take its place."""
    at = results.lower().find(LEGENDS_HEADING)
    if at < 0:
        return {}
    items, cur = [], None
    for line in results[at + len(LEGENDS_HEADING):].split("\n"):
        s = line.strip()
        m = LEGEND_START.match(s)
        if m:
            cur = [m.group(1), s[m.end():].lstrip(" .:")]
            items.append(cur)
        elif LEGEND_OTHER.match(s):
            cur = None
        elif cur is not None and s:
            cur[1] += " " + s
    out = {}
    for label, body in items:
        named = figures(label)
        if len(named) != 1 or named[0] in out:
            continue
        found = panel_markers(body)
        letters = set()
        for _, group in found:
            letters |= legend_letters(group) or set()
        out[named[0]] = {"label": label, "body": body, "letters": letters,
                         "title": body[:found[0][0]] if found else body}
    return out


def paper_legends(lib, key):
    path = os.path.join(lib, "texts", key, "results.txt")
    return figure_legends(read_text(path)) if os.path.exists(path) else {}


def figure_name(fig):
    kind, n = fig
    return {"main": f"Fig. {n}", "ext": f"Extended Data Fig. {n}", "supp": f"Fig. S{n}"}[kind]


def figure_place(fields):
    """A figures row's figure and panel as one place, as a draft cites it: "Fig. 2b-c"; "Fig. 3" for `all`."""
    panel = fields.get("panel", "")
    return fields.get("figure", "") + ("" if panel.strip().lower() == "all" else panel)


def legend_units(legs):
    """Every (figure, letter) the legends name; a figure whose legend names no letters gives (figure, "all")."""
    return {(fig, x) for fig, leg in legs.items() for x in (sorted(leg["letters"]) or ["all"])}


def row_letters(panel):
    """The letters a figures row's panel names ({"all"} for `all`), a word in parentheses ("b (left)") set aside; None
    if they cannot be read."""
    p = re.sub(r"\([^)]*\)", "", panel).strip()
    if p.lower() == "all":
        return {"all"}
    return legend_letters(p) or None


FIGURES_FIRST_VERSION_ROW = ("a row of the figures reading's first version ({} fields, not {}); read the paper again "
                             "with the current brief")
VERSION_FIELDS = {"v1": FIGURES_V1, "v2": FIELDS["figures"]}  # the fields of each fixed version's figures rows


def figure_names(row):
    """The fields of a figures row by its number of fields: the pilot's ten, version 1's eleven, or the current."""
    return {len(FIGURES_FIRST_VERSION): FIGURES_FIRST_VERSION, len(FIGURES_V1): FIGURES_V1}.get(len(row),
                                                                                              FIELDS["figures"])


def check_figure_row(row, label, text, legs, images=None, pdf=False):
    """(fields or None, figure or None, letters or None, fails, looks) of one figures row, by its version's checks:
    the fields and the paper's label; the evidence, the source (or the first version's kind) and the chart from their
    lists, a chart of the list but `not stated` and `other (...)` also as inferred (" (inferred)" after it, and nothing
    else) in version 1, and never in the current version, which reads the chart from the image; there, the marks, the
    targets shown, the colour and where the colour is named from their lists, one colour named nowhere but `not
    applicable`, and the image: the file the paper's figures list gives the figure (images: {figure: file}), a page of
    its PDF (pdf), or `none` for a figure that has no image there; the figure named once, its legend in the text; the
    panel's letters read and the legend's (`all` exactly when the legend names none); the evidence quote 8 to 40
    words, without an ellipsis, in the text."""
    names = figure_names(row)
    if len(row) != len(names):
        return None, None, None, [f"{len(row)} fields, not {len(names)}"], []
    f = dict(zip(names, row))
    fails, looks = [], []
    if f["paper"] != label:
        fails.append(f"the paper is {f['paper']!r}, not {label!r}")
    if names is not FIGURES_FIRST_VERSION:
        if label_of(f["evidence"]) not in FIGURE_EVIDENCE:
            fails.append(f"evidence {f['evidence']!r} is not one of the list")
        if f["source"] not in FIGURE_SOURCES:
            fails.append(f"source {f['source']!r} is not one of the list")
    elif label_of(f["kind"]) not in FIGURE_KINDS_FIRST_VERSION:
        fails.append(f"kind {f['kind']!r} is not one of the list")
    chart = chart_as_listed(f["chart"])
    if label_of(chart) not in FIGURE_CHARTS:
        fails.append(f"chart {f['chart']!r} is not one of the list")
    elif is_inferred(f["chart"]) and (label_of(chart) in NOT_INFERRED or names is FIELDS["figures"]):
        fails.append(f"chart {f['chart']!r}: " + ("`not stated` and `other (...)` are never inferred"
                                                  if label_of(chart) in NOT_INFERRED else
                                                  "the chart is read from the image, never inferred"))
    named = figures(f["figure"])
    fig = named[0] if len(named) == 1 else None
    if fig is None:
        fails.append(f"the figure field names {len(named)} figures: {f['figure']!r}")
    elif fig not in legs:
        fails.append(f"no legend of {figure_name(fig)} in the text")
    if names is FIELDS["figures"]:
        for field, listed in (("marks", FIGURE_MARKS), ("targets_shown", FIGURE_TARGETS), ("colour", FIGURE_COLOURS),
                              ("colour_named_by", FIGURE_COLOUR_NAMED_BY)):
            if label_of(f[field]) not in listed:
                fails.append(f"{field.replace('_', ' ')} {f[field]!r} is not one of the list")
        if (label_of(f["colour"]) == "one colour") != (f["colour_named_by"] == "not applicable"):
            fails.append("one colour is named nowhere, so its colour_named_by is `not applicable`, and only then")
        fails += image_fails(f["image"], fig, images or {}, pdf)
    letters = row_letters(f["panel"])
    if letters is None:
        fails.append(f"panel {f['panel']!r} is not read as letters or `all`")
    elif fig in legs:
        given = legs[fig]["letters"]
        if not given and letters != {"all"}:
            fails.append(f"the legend of {figure_name(fig)} names no panel letters, so the panel is `all`")
        elif given and "all" in letters:
            fails.append(f"the legend of {figure_name(fig)} names panels {''.join(sorted(given))}, not `all`")
        elif given and letters - given:
            fails.append(f"panel letters {''.join(sorted(letters - given))} are not in the legend of "
                         f"{figure_name(fig)}")
    quote = quote_fails("evidence_quote", f["evidence_quote"], text)
    fails += quote
    place = None if quote else place_of(f["evidence_quote"], fig, legs, text)
    if place == TWO_PLACES:
        fails.append("evidence_quote is not within one place of the text (a legend, the Results text, the Methods): "
                     "it joins two, or quotes a caption that is not read")
    elif place and place not in OWN_LEGEND and fig in legs:
        looks.append(f"the evidence quote is from {place}, not the figure's legend")
    check_numbers(f["n_stated"], text, looks, "n_stated")
    return f, fig, letters, fails, looks


def image_fails(image, fig, images, pdf):
    """Why a row's image is not the one its figure was read from: the file the paper's figures list gives the figure,
    `page N` of the paper's PDF, or `none` for a figure that has no image there."""
    if fig is not None and fig in images:
        return [] if image == images[fig] else [f"the image of {figure_name(fig)} is {images[fig]}, not {image!r}"]
    if image == NO_IMAGE or (pdf and PDF_PAGE.fullmatch(image)):
        return []
    return [f"image {image!r} is not one of the paper's figure images" +
            ("; a page of its PDF is written `page N`" if pdf else "; a figure without one is `none`")]


OWN_LEGEND = ("the legend's title", "the legend, after its title")
TWO_PLACES = "no one place of the text"


def place_of(quote, fig, legs, text):
    """Where in a paper's text (as source_text gives it) a quote of a figures row is, after the checks' normalisation:
    its figure's legend (its title, or after it), the Results text, another figure's legend, the Methods or another
    cut file; no one place (two joined, or a caption that is not a figure's legend); or "not found"."""
    q = normal(quote)
    if fig in legs and q in normal(legs[fig]["title"]):
        return "the legend's title"
    if fig in legs and q in normal(legs[fig]["body"]):
        return "the legend, after its title"
    files = text.split(FILE_BREAK)
    at = files[0].find(LEGENDS_HEADING)
    if q in (files[0][:at] if at >= 0 else files[0]):
        return "the Results text"
    other = [g for g in sorted(legs, key=fig_order) if g != fig and q in normal(legs[g]["body"])]
    if other:
        return f"the legend of {figure_name(other[0])}"
    if any(q in x for x in files[1:]):
        return "the Methods" if len(files) > 1 and q in files[1] else "another of the paper's cut files"
    return TWO_PLACES if q in text else "not found"


def no_conclusion(value):
    return normal(value).strip() in NO_CONCLUSION


def check_figures_reading(rows, label, text, legs, kept, current=True, images=None, pdf=False, version=None):
    """One reading's figures rows of one paper, checked: {rows: [(row, fails, looks)], passed, failed, conclusions,
    counts, units_not_read}. Each row by its version's checks and against the exclusion list; with current (a library's
    check), a row of the first version fails with its reason, and so does a row whose fields are not those of the
    fixed version its reading was started under (version). Each figure's conclusion: exactly one, on the figure's
    first row, 2 to 50 words, without an ellipsis, in its figure's legend or the Results text, where it was taken from
    recorded. A conclusion that fails is the figure's and leaves its rows in; a row that fails its own checks is left
    out of the agreement and the catalogue. images and pdf: the paper's figure images and whether it is read from a
    PDF, as check_figure_row takes them."""
    checked, passed, failed, by_fig, quotes = [], [], [], {}, 0
    shape = VERSION_FIELDS.get(version)
    for i, row in enumerate(rows, 1):
        f, fig, letters, fails, looks = check_figure_row(row, label, text, legs, images, pdf)
        old = len(row) == len(FIGURES_FIRST_VERSION)
        if current and old:
            fails.insert(0, FIGURES_FIRST_VERSION_ROW.format(len(row), len(FIELDS["figures"])))
        elif current and shape and f is not None and figure_names(row) is not shape:
            fails.insert(0, f"a row of {len(row)} fields in a reading started under {version}, whose rows have "
                            f"{len(shape)}")
        cited = kept.cited_in(" | ".join(row))
        if cited:
            fails.append(f"cites sources kept out: {', '.join(cited)}")
        quotes += f is not None and not (current and old) and not any(x.startswith("evidence_quote") for x in fails)
        if fig is not None:
            by_fig.setdefault(fig, []).append((i, f))
        checked.append((row, fails, looks))
        if fails:
            failed.append({"row": i, "figure": f["figure"] if f else "", "panel": f["panel"] if f else "",
                           "fails": fails})
        else:
            passed.append({**f, "row": i, "fig": fig, "letters": letters, "looks": looks})
    conclusions = {}
    for fig, frows in by_fig.items():
        given = [(i, f) for i, f in frows if not no_conclusion(f["conclusion_quote"])]
        out = {"rows": len(frows), "conclusions": len(given), "fails": [], "taken_from": None, "quote": None}
        if len(given) != 1:
            out["fails"].append(f"{len(given)} conclusions, not one")
        else:
            i, f = given[0]
            if i != frows[0][0]:
                out["fails"].append("the conclusion is not on the figure's first row")
            quote = quote_fails("conclusion_quote", f["conclusion_quote"], text, CONCLUSION_WORDS)
            out["quote"] = f["conclusion_quote"]
            out["taken_from"] = place_of(f["conclusion_quote"], fig, legs, text)
            if not quote and out["taken_from"] not in OWN_LEGEND + ("the Results text",):
                quote.append(f"conclusion_quote is from {out['taken_from']}, not its figure's legend or the Results "
                             "text")
            out["fails"] += quote
        conclusions[fig] = out
    covered = {(x["fig"], y) for x in passed for y in x["letters"]}
    units = legend_units(legs)
    counts = {"rows": len(rows), "passed": len(passed), "failed": len(failed), "quotes_found": quotes,
              "figures_read": len(by_fig), "figures_in_legends": len(legs),
              "figures_with_one_conclusion_found": sum(not v["fails"] for v in conclusions.values()),
              "legend_units": len(units), "legend_units_covered": len(units & covered)}
    return {"rows": checked, "passed": passed, "failed": failed, "conclusions": conclusions, "counts": counts,
            "units_not_read": sorted(f"{figure_name(fig)} {x}" for fig, x in units - covered)}


def figure_units(passed, labels):
    """{(figure, letter): {field: {value, "other" for any other (...)}, field_words: {value with the words of other
    (...)}, field_as_written: {value}, rows}} of one reading's passing rows, each row cut into its panels, so that
    readings that group panels differently still meet; a chart is compared without " (inferred)"."""
    out = {}
    for r in passed:
        for x in r["letters"]:
            u = out.setdefault((r["fig"], x), {"rows": []})
            for f in labels:
                value = chart_as_listed(r[f]) if f == "chart" else r[f]
                u.setdefault(f, set()).add(label_of(value))
                u.setdefault(f + "_words", set()).add(value)
                u.setdefault(f + "_as_written", set()).add(r[f])
            u["rows"].append(r["row"])
    return out


def same_span(a, b):
    a, b = normal(a or ""), normal(b or "")
    return bool(a and b) and (a in b or b in a)


def figure_agreement(readings, legs, labels=FIGURE_LABELS):
    """(measures, units) of two readings of one paper's figures, {1: checked, 2: checked} as check_figures_reading
    gives them: the panels both cover, of those either covers; on the panels both cover, the share whose two readings
    give the same value of each field (other (...) as one value, and with its words; a chart without " (inferred)"),
    and of all of them; the panels each reading gives an inferred chart; the quote check (rows whose evidence quote is
    found) and the conclusion check (figures with one conclusion found); of the conclusions both found, the share that
    are the same span and that were taken from the same place."""
    u1, u2 = (figure_units(readings[n]["passed"], labels) for n in FIGURE_READINGS)
    both, either = sorted(set(u1) & set(u2)), set(u1) | set(u2)

    def share(same):
        return round(sum(same(u1[u], u2[u]) for u in both) / max(1, len(both)), 3)
    m = {"units_in_legends": len(legend_units(legs)), "units_covered_by_either": len(either),
         "units_covered_by_both": len(both), "unit_agreement": round(len(both) / max(1, len(either)), 3)}
    for f in labels:
        m[f"{f}_agreement"] = share(lambda a, b: a[f] == b[f])
        if f != "source":
            m[f"{f}_agreement_with_other_words"] = share(lambda a, b: a[f + "_words"] == b[f + "_words"])
    m["_".join(labels[:-1]) + f"_and_{labels[-1]}_agreement"] = share(lambda a, b: all(a[f] == b[f] for f in labels))
    if labels == FIGURE_LABELS_V2:  # the three version 1 compares, together, as it measures them
        m["evidence_source_and_chart_agreement"] = share(lambda a, b: all(a[f] == b[f] for f in FIGURE_LABELS))
    if "chart" in labels:
        for n, units in zip(FIGURE_READINGS, (u1, u2)):
            m[f"units_with_chart_inferred_{n}"] = sum(any(map(is_inferred, u["chart_as_written"]))
                                                      for u in units.values())
    counts = [readings[n]["counts"] for n in FIGURE_READINGS]
    m["quote_check"] = round(sum(c["quotes_found"] for c in counts) / max(1, sum(c["rows"] for c in counts)), 3)
    m["conclusion_check"] = round(sum(c["figures_with_one_conclusion_found"] for c in counts) /
                                  max(1, sum(c["figures_read"] for c in counts)), 3)
    c1, c2 = (readings[n]["conclusions"] for n in FIGURE_READINGS)
    figs = sorted(set(c1) & set(c2))
    found = [(c1[f], c2[f]) for f in figs if c1[f]["taken_from"] not in (None, "not found")
             and c2[f]["taken_from"] not in (None, "not found")]
    m.update(figures_read_by_both=len(figs), conclusions_found_by_both=len(found),
             same_conclusion_span=round(sum(same_span(a["quote"], b["quote"]) for a, b in found) /
                                        max(1, len(found)), 3),
             same_conclusion_taken_from=round(sum(a["taken_from"] == b["taken_from"] for a, b in found) /
                                              max(1, len(found)), 3))
    units = []
    for u in sorted(either, key=lambda u: (u[0][0] != "main", u[0][0], u[0][1], u[1])):
        a, b = u1.get(u), u2.get(u)
        entry = {"figure": figure_name(u[0]), "panel": u[1], "rows_1": a["rows"] if a else [],
                 "rows_2": b["rows"] if b else []}
        for f in labels:
            entry[f"{f}_1"] = "; ".join(sorted(a[f + "_as_written"])) if a else ""
            entry[f"{f}_2"] = "; ".join(sorted(b[f + "_as_written"])) if b else ""
            entry[f"{f}_agrees"] = bool(a and b and a[f] == b[f])
        units.append(entry)
    return m, units


def figure_labels(readings):
    """What two readings of a paper are compared on: the current version's fields where every row that passes is of
    it, else version 1's three."""
    rows = [x for c in readings.values() for x in c["passed"]]
    return FIGURE_LABELS_V2 if all("marks" in x for x in rows) else FIGURE_LABELS


def paper_images(lib, key):
    """{figure: image file} of a paper's main figures, from the list `fetch --figure-images` wrote; {} without one."""
    path = os.path.join(lib, "sources", key, FIGURE_IMAGES)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {figures(x["figure"])[0]: x["file"] for x in csv.DictReader(fh, delimiter="\t") if figures(x["figure"])}


def paper_pdf(lib, key):
    """Whether a paper is read from its PDF: no full text fetched, its PDF given."""
    folder = os.path.join(lib, "sources", key)
    return (not os.path.exists(os.path.join(folder, "fulltext.xml")) and
            os.path.exists(os.path.join(folder, "paper.pdf")))


def figure_review(units, readings, labels=FIGURE_LABELS):
    """The panels one reading covers and the other does not, the legends' panels neither covers, the panels whose
    fields the two readings give differently, the rows that failed their checks, and the conclusions that failed."""
    lines = []
    for e in units:
        where = f"{e['figure']} {e['panel']}"
        if not (e["rows_1"] and e["rows_2"]):
            lines.append(f"one reading only | {where} | reading {1 if e['rows_1'] else 2}")
            continue
        lines += [f"{f} differs | {where} | {e[f + '_1']} / {e[f + '_2']}" for f in labels if not e[f"{f}_agrees"]]
    neither = set(readings[FIGURE_READINGS[0]]["units_not_read"]) & set(readings[FIGURE_READINGS[1]]["units_not_read"])
    lines += [f"read by neither | {x}" for x in sorted(neither)]
    for n in FIGURE_READINGS:
        lines += [f"failed check | reading {n} row {x['row']} | {x['figure']} {x['panel']} | {'; '.join(x['fails'])}"
                  for x in readings[n]["failed"]]
        lines += [f"conclusion | reading {n} | {figure_name(fig)} | {'; '.join(v['fails'])}"
                  for fig, v in sorted(readings[n]["conclusions"].items()) if v["fails"]]
    return lines


def agree_figures(lib, key):
    """Two readings of one paper's figures (rows/<key>/figures_1 and figures_2): every row of each checked, the rows
    cut into panels, the measures, held to the pass line where both readings were started under one fixed version, and
    the review list; written to checks/<key>.figures_agreement.json."""
    r = paper(papers(lib), key)
    text, kept, legs = source_text(lib, key), KeptOut.of(lib), paper_legends(lib, key)
    images, pdf = paper_images(lib, key), paper_pdf(lib, key)
    versions = {n: recorded_version(rows_file(lib, key, "figures", n)) for n in FIGURE_READINGS}
    readings = {}
    for n in FIGURE_READINGS:
        path = rows_file(lib, key, "figures", n)
        if not os.path.exists(path):
            raise LibraryError(f"no rows at {path}; both readers write theirs first")
        readings[n] = check_figures_reading(table_rows(read_text(path)), r["label"], text, legs, kept, images=images,
                                            pdf=pdf, version=fixed_version("figures", versions[n]))
    labels = figure_labels(readings)
    m, units = figure_agreement(readings, legs, labels)
    fixed = {fixed_version("figures", v) for v in versions.values()}
    held = len(fixed) == 1 and None not in fixed
    line = FIGURE_PASS_V2 if labels == FIGURE_LABELS_V2 else FIGURE_PASS
    if held:
        m["pass"] = {name: m[name] >= at for name, at in line.items()}
        m["passes"] = all(m["pass"].values())
    review = figure_review(units, readings, labels)
    read = files_read(lib, [rows_file(lib, key, "figures", n) for n in FIGURE_READINGS])
    report = {"key": key, "label": r["label"], "read": read, "versions": versions, "labels": list(labels),
              "measures": m,
              "readings": {n: {"counts": x["counts"], "failed": x["failed"],
                               "conclusions": {figure_name(f): v for f, v in x["conclusions"].items()},
                               "units_not_read": x["units_not_read"],
                               "looks": [{"row": p["row"], "looks": p["looks"]} for p in x["passed"] if p["looks"]]}
                           for n, x in readings.items()},
              "review": review, "units": units}
    path = os.path.join(lib, "checks", f"{key}.figures_agreement.json")
    write_text(path, json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    out = [f"{key} ({r['label']}): two readings of the figures"]
    for n, x in readings.items():
        c = x["counts"]
        out.append(f"  reading {n}: {c['rows']} rows, {c['passed']} pass their checks; "
                   f"{c['figures_with_one_conclusion_found']} of {c['figures_read']} figures have one conclusion, on "
                   f"their first row, found in the text; the rows that pass cover {c['legend_units_covered']} of the "
                   f"legends' {c['legend_units']} panels")
    out += [f"  panels both readings cover: {m['units_covered_by_both']} of the {m['units_covered_by_either']} either "
            f"covers ({m['unit_agreement']:.2f})",
            f"  quote check {m['quote_check']:.2f}; conclusion check {m['conclusion_check']:.2f}",
            f"  on the panels both cover: evidence {m['evidence_agreement']:.2f} "
            f"({m['evidence_agreement_with_other_words']:.2f} with the words of other (...)); source "
            f"{m['source_agreement']:.2f}; chart {m['chart_agreement']:.2f} "
            f"({m['chart_agreement_with_other_words']:.2f} with the words); all three "
            f"{m['evidence_source_and_chart_agreement']:.2f}",
            f"  charts given as inferred, compared without \"{INFERRED.strip()}\": on "
            f"{counted(m['units_with_chart_inferred_1'], 'panel')} in reading 1 and "
            f"{m['units_with_chart_inferred_2']} in reading 2",
            f"  conclusions both found: {m['conclusions_found_by_both']} of {m['figures_read_by_both']} figures both "
            f"read; the same span {m['same_conclusion_span']:.2f}, taken from the same place "
            f"{m['same_conclusion_taken_from']:.2f}",
            ]
    if labels == FIGURE_LABELS_V2:
        out.insert(6, "  from the images, on the panels both cover: " + "; ".join(
            f"{f.replace('_', ' ')} {m[f + '_agreement']:.2f}" for f in FIGURE_LABELS_V2[len(FIGURE_LABELS):]))
    if held:
        out += [f"  under {next(iter(fixed))}, against the pass line:"]
        out += [f"    {name.replace('_', ' ')} {m[name]:.2f}, at least {at}: " +
                ("passes" if m["pass"][name] else "does not pass") for name, at in line.items()]
        out.append("  the paper passes" if m["passes"] else "  the paper does not pass: " +
                   ", ".join(k.replace("_", " ") for k, ok in m["pass"].items() if not ok))
    else:
        out.append("  not held to the pass line: the two readings were not both started under one fixed version of "
                   f"the reading ({', '.join(VERSIONS['figures'])})")
    out += ["review list:"] + [f"  - {x}" for x in review] if review else ["review list: empty"]
    return out + [f"written to {path}"]


# -- the calibration review page --------------------------------------------------------------------------------------

# The page is the same for the figures and the moves readings but for what REVIEW gives: the fields with closed lists
# (labels), the quotes, the field shown with an example, the name of a reading's two sides, the unit two sides are
# compared on (a figure's panel; a move, as `agree` matches two pipelines' verified rows), the briefs, the agreement
# file and its measures (with, for a share of the units both sides give, where its number is: bases), the lines of
# `agree`'s review list that the page shows side by side (differs), and the field whose values may be inferred.
REVIEW = {
    "figures": {"labels": FIGURE_LABELS, "quotes": ("evidence_quote",), "shown": "readout", "side": "reading",
                "inferred": "chart",
                "sides": FIGURE_READINGS, "unit": "panels", "briefs": ("figure-reader.md",),
                "agreement": "figures_agreement.json", "agree": " --reading figures",
                "differs": re.compile(r"^\w+ differs \|"),
                "measures": (("unit_agreement", "panels both cover"), ("evidence_agreement", "evidence"),
                             ("source_agreement", "source"), ("chart_agreement", "chart"),
                             ("evidence_source_and_chart_agreement", "all three"), ("quote_check", "quote check"),
                             ("conclusion_check", "conclusion check")),
                "bases": {"unit_agreement": ("units_covered_by_either",),
                          **{k: ("units_covered_by_both",) for k in ("evidence_agreement", "source_agreement",
                                                                     "chart_agreement",
                                                                     "evidence_source_and_chart_agreement")}}},
    "moves": {"labels": ("kind", "module", "relation"), "quotes": QUOTES, "shown": "question", "side": "pipeline",
              "inferred": None, "sides": PIPELINES, "unit": "moves", "briefs": ("moves-reader.md", "moves-verifier.md"),
              "agreement": "agreement.json", "agree": "", "differs": re.compile(r"^modules differ"),
              "measures": (("quote_check", "quote check"), ("row_agreement", "rows"), ("module_agreement", "module"),
                           ("relation_agreement", "relation"), ("kind_agreement", "kind")),
              "bases": {k: ("items_by_support", "2") for k in ("module_agreement", "relation_agreement",
                                                               "kind_agreement")}}}
# a reader's report (its reply, naming its hard calls) is kept beside its rows as report.md; where none is kept
REPORT = "report.md"
NO_REPORTS = {
    "figures": "No report of a figure reader is kept for these papers: a reader names its hard calls in its reply "
               "(agents/figure-reader.md, \"How you work\"), which the procedure keeps as report.md beside its rows.",
    "moves": "No report of a moves reader is kept for these papers: each names its hard calls in its reply, which "
             "the procedure keeps as report.md beside its rows (a verifier is not given its reader's report)."}
PLUGIN_FOLDER = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
EXAMPLES = 3
REVIEW_STYLE = """
pre.report { white-space: pre-wrap; font-size: 0.85em; }
:root{--bg:#fff;--fg:#1d1d1f;--muted:#66666c;--line:#d8d8de;--card:#f5f5f7;--mark:#ffef9f;--diff:#fbe0dd;
--link:#245c91}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#e6e6e9;--muted:#a0a0a6;--line:#3a3a40;--card:#212125;
--mark:#5c4c00;--diff:#4d2622;--link:#8fb9e3}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,
sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5em;margin:0 0 4px}
h2{margin-top:2.2em;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{margin-top:1.6em}
h4{margin:.2em 0 .4em}
a{color:var(--link)}
nav a{margin-right:14px}
.muted{color:var(--muted)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:.92em}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.case{border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin:12px 0;background:var(--card)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:760px){.pair{grid-template-columns:1fr}}
.row{background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:6px 8px;margin-bottom:8px}
.rowhead{font-weight:600;margin-bottom:2px}
table.fields{margin:0}
table.fields th{width:9.5em;color:var(--muted);font-weight:normal}
tr.diff td{background:var(--diff);font-weight:600}
.legend,.said{border-left:3px solid var(--line);padding-left:10px;margin:6px 0;font-size:.93em}
mark{background:var(--mark);color:inherit}
q{font-style:italic}
details{margin:4px 0 12px}
summary{cursor:pointer;color:var(--link)}
ul.examples{margin:4px 0;padding-left:20px}
footer{margin-top:3em;font-size:.85em;color:var(--muted)}
code{overflow-wrap:break-word}
footer code,p.muted code{word-break:break-all}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def and_list(words, last="and"):
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" {last} " + words[-1]


def counted(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def shown_number(x):
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def as_written(value):
    """A value as a JSON file keeps it: tuples as lists, numbers as keys as strings."""
    return json.loads(json.dumps(value))


def still_as_stored(fresh, stored):
    """Whether what the rows give now is what a file wrote of them; of a dict, the keys given now."""
    for k, v in as_written(fresh).items():
        s = stored.get(k)
        if isinstance(v, dict):
            if not isinstance(s, dict) or any(s.get(name) != x for name, x in v.items()):
                return False
        elif s != v:
            return False
    return True


def measured_on(measures, path):
    """The number of units a stored measure is a share of, by its path in the measures; None where it is not known."""
    for k in path or ():
        measures = measures.get(k) if isinstance(measures, dict) else None
    return measures if path else None


def file_sha256(path):
    if not os.path.exists(path):
        return "not found"
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


FIGURE_LISTS = {"evidence": FIGURE_EVIDENCE, "source": FIGURE_SOURCES, "chart": FIGURE_CHARTS, "marks": FIGURE_MARKS,
                "targets_shown": FIGURE_TARGETS, "colour": FIGURE_COLOURS, "colour_named_by": FIGURE_COLOUR_NAMED_BY}
# the measures a review's table gives beside version 1's, for papers read under the current version
FIGURE_MEASURES_V2 = (("marks_agreement", "marks"), ("targets_shown_agreement", "targets shown"),
                      ("colour_agreement", "colour"), ("colour_named_by_agreement", "colour named by"))


def review_lists(lib, reading, labels=None):
    """The closed lists of a reading, field by field, in the briefs' order: for the figures, those of the fields
    compared (labels), version 1's by default."""
    if reading == "figures":
        return {f: FIGURE_LISTS[f] for f in labels or FIGURE_LABELS}
    return {"kind": KINDS, "module": tuple(module_names(lib)) + ("none",), "relation": RELATIONS}


def batch_labels(reading, batch):
    """The fields with closed lists a review compares: for the figures, the current version's where every row of the
    batch is of it, else version 1's three."""
    if reading != "figures":
        return REVIEW[reading]["labels"]
    rows = [x for p in batch for s in p["sides"].values() for x in s["rows"]]
    return FIGURE_LABELS_V2 if all("marks" in x for x in rows) else FIGURE_LABELS


def fig_order(fig):
    """Main figures first, by number, then the Extended Data and the Supplementary figures."""
    return fig[0] != "main", fig[0], fig[1]


def row_place(reading, x):
    return f"{x['figure']} {x['panel']}" if reading == "figures" else x["panel"]


def row_panels(reading, x):
    """[(figure, letter)] of a row: a figures row's letters (`all` for a figure without), a move's panel tokens (`*`
    for a figure named whole)."""
    if reading == "figures":
        return [(x["fig"], y) for y in sorted(x["letters"])]
    return sorted(((k, n), y) for k, n, y in panel_tokens(x["panel"]))


def row_figures(reading, x):
    return {fig for fig, _ in row_panels(reading, x)}


def legend_part(leg, letter):
    """A legend's text for one panel: from the marker that names its letter to the next marker; the whole legend for
    a figure named whole (`all`, `*`) or a letter no marker names."""
    body = leg["body"]
    found = panel_markers(body)
    for i, (at, group) in enumerate(found):
        if letter in (legend_letters(group) or ()):
            return body[at:found[i + 1][0] if i + 1 < len(found) else len(body)].strip()
    return body


def legend_texts(legs, panels):
    """[{label, title, parts}] of the legends of the panels' figures that are in the text: each legend's title and its
    parts for the panels, or its whole text."""
    parts = {}
    for fig, letter in panels:
        if fig in legs:
            parts.setdefault(fig, []).append(legend_part(legs[fig], letter))
    out = []
    for fig in sorted(parts, key=fig_order):
        leg, given = legs[fig], list(dict.fromkeys(parts[fig]))
        whole = leg["body"] in given
        out.append({"label": leg["label"], "title": "" if whole else leg["title"].strip(),
                    "parts": [leg["body"]] if whole else given})
    return out


def marked(text, quotes):
    """A text, escaped, with the first place each quote is in it (as written, case aside) marked."""
    spans = []
    for q in quotes:
        m = re.search(re.escape(q.strip()), text, re.I) if q.strip() else None
        if m:
            spans.append(m.span())
    out, pos = [], 0
    for a, b in sorted(spans):
        if a >= pos:
            out += [esc(text[pos:a]), "<mark>", esc(text[a:b]), "</mark>"]
            pos = b
    return "".join(out + [esc(text[pos:])])


def quote_sentence(quote, text):
    """The sentence of a text that holds a quote (after the checks' normalisation), or two that hold it together;
    "" if none does."""
    q = normal(quote)
    said = re.split(r"(?<=[.!?])\s+(?=[A-Z])", re.sub(r"\s+", " ", text))
    for width in (1, 2):
        for i in range(len(said) - width + 1):
            s = " ".join(said[i:i + width])
            if q in normal(s):
                return s
    return ""


def review_figure_sides(lib, r, text, kept, legs, paths):
    """(sides, what `agree` would write, the panels read differently) of a paper's figures readings."""
    images, pdf = paper_images(lib, r["key"]), paper_pdf(lib, r["key"])
    readings = {n: check_figures_reading(table_rows(read_text(p)), r["label"], text, legs, kept, images=images, pdf=pdf,
                                         version=fixed_version("figures", recorded_version(p)))
                for n, p in paths.items()}
    sides = {n: {"rows": [dict(x, units={(x["fig"], y) for y in x["letters"]}) for x in c["passed"]],
                 "written": c["counts"]["rows"]} for n, c in readings.items()}
    if len(readings) < 2:
        return sides, None, []
    labels = figure_labels(readings)
    m, units = figure_agreement(readings, legs, labels)
    differ = []
    for e in units:
        fields = [f for f in labels if not e[f + "_agrees"]]
        if not (e["rows_1"] and e["rows_2"] and fields):
            continue
        fig, rows = figures(e["figure"])[0], {1: e["rows_1"], 2: e["rows_2"]}
        last = differ[-1] if differ else None
        if last and (last["fig"], last["rows"], last["fields"]) == (fig, rows, fields):
            last["panels"].append((fig, e["panel"]))  # panels the same rows read differently in the same way
        else:
            differ.append({"fig": fig, "panels": [(fig, e["panel"])], "rows": rows, "fields": fields})
    for d in differ:
        d["where"] = f"{figure_name(d['fig'])} {', '.join(y for _, y in d['panels'])}"
    return sides, {"measures": m, "units": units, "review": figure_review(units, readings, labels)}, differ


def review_move_sides(lib, r, text, kept, legs, paths):
    """(sides, what `agree` would write, the moves read differently) of a paper's moves pipelines: each verified row
    that passes its checks, its unit the move `agree` matched it into."""
    final, written = {}, {}
    for n, p in paths.items():
        final[n], _, c = pipeline_rows(lib, p, r["label"], text, kept)
        written[n] = c["rows"]
    if len(final) < 2:
        return {n: {"rows": [dict(x, units={i}) for i, x in enumerate(rows)], "written": written[n]}
                for n, rows in final.items()}, None, []
    items, m = agreement(final)
    item_of = {(p, i): j for j, it in enumerate(items) for p, i in it.items()}
    sides = {n: {"rows": [dict(x, units={item_of[(n, i)]}) for i, x in enumerate(rows)], "written": written[n]}
             for n, rows in final.items()}
    differ = []
    for it in items:
        if len(it) < 2:
            continue
        pair = [final[p][i] for p, i in sorted(it.items())]
        fields = [f for f in REVIEW["moves"]["labels"] if len({label_of(x[f]) for x in pair}) > 1]
        if fields:
            differ.append({"fig": None, "panels": sorted({t for x in pair for t in row_panels("moves", x)}),
                           "rows": {p: [final[p][i]["row"]] for p, i in sorted(it.items())}, "fields": fields,
                           "where": " / ".join(dict.fromkeys(x["panel"] for x in pair))})
    return sides, {"measures": m, "items": moves_items(final, items)}, differ


def reviewed(lib, key, reading):
    """Whether a paper's reading has an agreement file or a check, the two a review is built from."""
    if os.path.exists(os.path.join(lib, "checks", f"{key}.{REVIEW[reading]['agreement']}")):
        return True
    path = os.path.join(lib, "checks", f"{key}.json")
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as fh:
        return reading in json.load(fh)


def review_paper(lib, r, reading):
    """One paper's reading as the review shows it: a paper read twice from its agreement file, its rows checked again
    and refused if they no longer give what `agree` wrote; a paper read once from its check, refused if its rows
    changed after it. {key, label, stored, kept, sides: {n: {rows, written}}, differ, review, legs, results, whole}."""
    key, spec = r["key"], REVIEW[reading]
    text, kept, legs = source_text(lib, key), KeptOut.of(lib), paper_legends(lib, key)
    check_path, agreement_path = (os.path.join(lib, "checks", f"{key}.{name}")
                                  for name in ("json", spec["agreement"]))
    checked = {}
    if os.path.exists(check_path):
        with open(check_path, encoding="utf-8") as fh:
            checked = json.load(fh).get(reading) or {}
    stored = None
    if os.path.exists(agreement_path):
        with open(agreement_path, encoding="utf-8") as fh:
            stored = json.load(fh)
        paths = {n: rows_file(lib, key, reading, n) for n in spec["sides"]}
        missing = [p for p in paths.values() if not os.path.exists(p)]
        if missing:
            raise LibraryError(f"{key}: no rows at {missing[0]}, which `agree` read")
        again = f"run papers.py agree {lib} {key}{spec['agree']} again"
        if not stored.get("read"):
            raise LibraryError(f"{key}: its agreement file does not record the rows `agree` read; {again}")
        changed = [f for f, sha in sorted(stored["read"].items())
                   if text_sha1(os.path.join(lib, *f.split("/"))) != sha]
        if changed:
            raise LibraryError(f"{key}: {changed[0]} changed after `agree`; {again}")
    elif checked:
        path = os.path.join(lib, *checked["file"].split("/"))
        written = read_text(path) if os.path.exists(path) else ""
        if hashlib.sha1(written.encode("utf-8")).hexdigest() != checked["sha1"]:
            raise LibraryError(f"{key} {reading}: its rows changed after the check; check it again")
        m = re.search(r"/(?:figures|pipe)_(\d+)/", checked["file"])
        paths = {int(m.group(1)) if m else 1: path}
    else:
        raise LibraryError(f"{key} has no {reading} reading checked or agreed; run check or agree first")
    build = review_figure_sides if reading == "figures" else review_move_sides
    sides, fresh, differ = build(lib, r, text, kept, legs, paths)
    if stored is not None and not still_as_stored(fresh, stored):
        raise LibraryError(f"{key}: its rows no longer give what `agree` wrote; run papers.py agree {lib} {key}"
                           f"{spec['agree']} again")
    kept_side = next((n for n, p in paths.items()
                      if os.path.relpath(p, lib).replace(os.sep, "/") == checked.get("file")), None)
    if stored is None and [i for i, x in enumerate(checked["rows"], 1) if not x["fails"]] != \
            [x["row"] for x in sides[kept_side]["rows"]]:
        raise LibraryError(f"{key} {reading}: its rows no longer give what the check wrote; check it again")
    folder = os.path.join(lib, "texts", key)
    results = read_text(os.path.join(folder, "results.txt")) if os.path.exists(os.path.join(folder, "results.txt")) \
        else ""
    at = results.lower().find(LEGENDS_HEADING)
    methods = read_text(os.path.join(folder, "methods.txt")) if os.path.exists(os.path.join(folder, "methods.txt")) \
        else ""
    said = re.sub(r"(?m)^#.*$", "", results[:at] if at >= 0 else results)  # the Results text without its headings
    return {"key": key, "label": r["label"], "stored": stored, "kept": kept_side, "sides": sides, "differ": differ,
            "review": [x for x in stored["review"] if not spec["differs"].match(x)] if stored else [],
            "legs": legs, "results": said, "whole": results + "\n" + methods}


def review_row(reading, x, name, differs=()):
    """A row's fields as a small table, the fields that differ marked; the paper's label is the section's."""
    quotes = REVIEW[reading]["quotes"] + (("conclusion_quote",) if reading == "figures" else ())
    cells = []
    for f in [f for f in FIELDS[reading][1:] if f in x or reading != "figures"]:  # a row's own version's fields
        v = x.get(f, "")
        if f == "conclusion_quote" and no_conclusion(v):
            continue
        cls = ' class="diff"' if f in differs else ""
        shown = f"<q>{esc(v)}</q>" if f in quotes else esc(v)
        cells.append(f"<tr{cls}><th>{esc(f.replace('_', ' '))}</th><td>{shown}</td></tr>")
    return (f'<div class="row"><div class="rowhead">{esc(name)}, row {x["row"]}</div>'
            f'<table class="fields">{"".join(cells)}</table></div>')


def legend_html(entries, quotes):
    if not entries:
        return '<p class="muted">The legend of this figure is not in the cut text.</p>'
    out = []
    for e in entries:
        pieces = [marked(x, quotes) for x in [e["title"]] + e["parts"] if x]
        out.append(f'<div class="legend"><b>{esc(e["label"])}.</b> {"<br>".join(pieces)}</div>')
    return "".join(out)


def spread(rows, k=EXAMPLES):
    """Up to k of the rows, one from each paper in turn, each paper's in the order given."""
    by_paper = {}
    for e in rows:
        by_paper.setdefault(e[0], []).append(e)
    out, i = [], 0
    while len(out) < k and any(i < len(v) for v in by_paper.values()):
        out += [v[i] for _, v in sorted(by_paper.items()) if i < len(v)][:k - len(out)]
        i += 1
    return out


def two_rows(reading, rows):
    """The two rows a paper shows beside its text, by a fixed rule: the first row of its first main figure and the
    first row of its last; with one main figure, its first row and its last; with no main figure, the same over all
    its figures."""
    figs = sorted({f for x in rows for f in row_figures(reading, x)}, key=fig_order)
    main = [f for f in figs if f[0] == "main"] or figs
    if not main:
        return rows[:1]
    first = [x for x in rows if main[0] in row_figures(reading, x)]
    last = [x for x in rows if main[-1] in row_figures(reading, x)]
    picked = [first[0], last[0] if main[-1] != main[0] else first[-1]]
    return picked[:1] if picked[1] is picked[0] else picked


def review_header(reading, batch):
    spec = REVIEW[reading]
    side, sides = spec["side"], spec["sides"]
    v2 = reading == "figures" and batch_labels(reading, batch) == FIGURE_LABELS_V2
    measures = spec["measures"] + (FIGURE_MEASURES_V2 if v2 else ())
    bases = {**spec["bases"], **({k: ("units_covered_by_both",) for k, _ in FIGURE_MEASURES_V2} if v2 else {})}
    heads = (["paper", "key"] + [f"rows, {side} {n}" for n in sides] + ["the paper's rows (check)"] +
             [label for _, label in measures] + ["pass line"])
    lines = []
    for p in batch:
        cells = [esc(p["label"]), f"<code>{esc(p['key'])}</code>"]
        cells += [f"{p['sides'][n]['written']}, {len(p['sides'][n]['rows'])} pass" if n in p["sides"] else "-"
                  for n in sides]
        cells.append(f"{side} {p['kept']}" if p["kept"] else "not checked")
        m = (p["stored"] or {}).get("measures", {})
        measured = [("none" if measured_on(m, bases.get(k)) == 0 else f"{m[k]:.2f}") if k in m else
                    "read once" for k, _ in measures]
        measured.append(("passes" if m["passes"] else "does not pass") if "passes" in m else "-")
        lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) +
                     "".join(f'<td class="n">{c}</td>' for c in measured) + "</tr>")
    line = ("The pass line, per paper: " + "; ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in PASS.items()) + "."
            if reading == "moves" else
            "The pass line, per paper whose two readings were started under one fixed version: " +
            "; ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in (FIGURE_PASS_V2 if v2 else FIGURE_PASS).items()) +
            ". A paper whose readings were not is measured, not held to it (-).")
    unit = "panels" if reading == "figures" else "moves both pipelines found"
    return (f'<section id="papers"><h2>1. The papers</h2><p>{counted(len(batch), "paper")}, each with its rows per '
            f"{side} (rows written, and those that pass their checks) and the measures <code>agree</code> wrote: the "
            f"agreements are shares of the {unit}, with <code>other (...)</code> as one value (none where there are no "
            f"such {spec['unit']}). {esc(line)}</p>"
            '<div class="scroll"><table><tr>' + "".join(f"<th>{esc(h)}</th>" for h in heads) + "</tr>" +
            "".join(lines) + "</table></div></section>")


def review_disagreements(reading, batch):
    spec = REVIEW[reading]
    side, fields = spec["side"], and_list([f.replace("_", " ") for f in batch_labels(reading, batch)], "or")
    total = sum(len(p["differ"]) for p in batch)
    out = [f'<section id="disagreements"><h2>2. Where the two {side}s disagree</h2>',
           f"<p>{counted(total, 'place')} where a paper's two {side}s give a different {esc(fields)}, with the "
           f"legend's text for them and each {side}'s rows; the field that differs is marked. Only rows that pass "
           "their checks are compared.</p>"]
    for p in batch:
        if p["stored"] is None:
            continue
        out.append(f"<h3>{esc(p['label'])}</h3>")
        if not p["differ"]:
            out.append('<p class="muted">None.</p>')
        rows = {n: {x["row"]: x for x in s["rows"]} for n, s in p["sides"].items()}
        for d in p["differ"]:
            shown = [(n, rows[n][i]) for n in sorted(d["rows"]) for i in d["rows"][n]]
            quotes = [x[q] for _, x in shown for q in spec["quotes"]]
            verb = "differs" if len(d["fields"]) == 1 else "differ"
            out.append(f'<div class="case"><h4>{esc(d["where"])}: {esc(and_list(d["fields"]))} {verb}</h4>' +
                       legend_html(legend_texts(p["legs"], d["panels"]), quotes) + '<div class="pair">' +
                       "".join("<div>" + "".join(review_row(reading, x, f"{side} {n}", d["fields"])
                                                 for m, x in shown if m == n) + "</div>" for n in sorted(d["rows"])) +
                       "</div></div>")
    out.append("<h3>The rest of the review list <code>agree</code> wrote</h3>")
    if not any(p["review"] for p in batch):
        out.append('<p class="muted">None.</p>')
    for p in batch:
        if p["review"]:
            out.append(f"<p><b>{esc(p['label'])}</b></p><ul>" + "".join(f"<li>{esc(x)}</li>" for x in p["review"]) +
                       "</ul>")
    return "".join(out) + "</section>"


def review_other(reading, batch):
    spec = REVIEW[reading]
    groups = {}
    for pi, p in enumerate(batch):
        for n, s in sorted(p["sides"].items()):
            for x in s["rows"]:
                for f in batch_labels(reading, batch):
                    if label_of(x[f]) != "other":
                        continue
                    m = re.fullmatch(r"other \((.*)\)", x[f].strip(), re.S)
                    words = m.group(1).strip() if m else ""
                    groups.setdefault(" ".join(words.lower().split()), []).append((pi, n, x, f, words))
    order = sorted(groups, key=lambda k: (-len({e[0] for e in groups[k]}), -len(groups[k]), k))
    out = ['<section id="other"><h2>3. The rows under <code>other (...)</code></h2>',
           f"<p>Of the rows that pass their checks, {counted(sum(len(v) for v in groups.values()), 'row')} under "
           f"<code>other (...)</code> in "
           f"{esc(and_list([f.replace('_', ' ') for f in batch_labels(reading, batch)], 'or'))}, in "
           f"{counted(len(groups), 'group')} "
           "of words, the groups that recur in the most papers first.</p>"]
    if not groups:
        out.append('<p class="muted">None.</p>')
    for k in order:
        entries = groups[k]
        out.append(f"<h3>{esc(entries[0][4] or '(no words)')}</h3><p class=\"muted\">"
                   f"{counted(len(entries), 'row')} in {counted(len({e[0] for e in entries}), 'paper')}; "
                   f"{esc(and_list(sorted({e[3] for e in entries})))}</p>"
                   f'<div class="scroll"><table><tr><th>paper</th><th>{esc(spec["side"])}</th><th>row</th>'
                   f"<th>figure and panel</th><th>field</th><th>{esc(spec['shown'])}</th><th>quote</th></tr>")
        for pi, n, x, f, _ in entries:
            out.append(f"<tr><td>{esc(batch[pi]['label'])}</td><td class=\"n\">{n}</td><td class=\"n\">{x['row']}</td>"
                       f"<td>{esc(row_place(reading, x))}</td><td>{esc(f)}</td><td>{esc(x[spec['shown']])}</td>"
                       f"<td><q>{esc(x[spec['quotes'][0]])}</q></td></tr>")
        out.append("</table></div>")
    return "".join(out) + "</section>"


def review_closed_lists(reading, batch, lists):
    spec = REVIEW[reading]
    side, sides, unit, guessed = spec["side"], spec["sides"], spec["unit"], spec["inferred"]
    out = ['<section id="lists"><h2>4. The closed lists</h2>',
           f"<p>Each list's values in the brief's order, with the papers and the {unit} each {side} gives it (of the "
           f"rows that pass their checks), the {unit} both {side}s give it alike (as <code>agree</code> counts a "
           f"{unit[:-1]} alike: the same values from the two {side}s' rows for it), and up to {EXAMPLES} examples "
           "from different papers." +
           (f" A {guessed} written with <code>{esc(INFERRED.strip())}</code> (one the legend does not name) counts "
            f"as its value, and beside each value are the {unit} each {side} gives it as inferred."
            if guessed else "") + "</p>"]
    for fi, (field, values) in enumerate(lists.items()):
        found, given = {}, {}
        for pi, p in enumerate(batch):
            for n, s in sorted(p["sides"].items()):
                for x in s["rows"]:
                    value = label_of(chart_as_listed(x[field]) if field == guessed else x[field])
                    e = found.setdefault(value, {"papers": set(), "units": {}, "inferred": {}, "rows": []})
                    e["papers"].add(pi)
                    e["units"].setdefault(n, set()).update((pi, u) for u in x["units"])
                    if field == guessed and is_inferred(x[field]):
                        e["inferred"].setdefault(n, set()).update((pi, u) for u in x["units"])
                    e["rows"].append((pi, n, x))
                    for u in x["units"]:
                        given.setdefault((pi, u), {}).setdefault(n, set()).add(value)
        alike = {u: v[sides[0]] for u, v in given.items() if all(n in v for n in sides) and
                 all(v[n] == v[sides[0]] for n in sides)}
        shown = list(values) + sorted(v for v in found if v not in values)
        table, blocks = [], []
        for vi, value in enumerate(shown):
            e = found.get(value, {"papers": set(), "units": {}, "inferred": {}, "rows": []})
            per_side = [len(e["units"].get(n, ())) for n in sides]
            both = sum(value in v for v in alike.values())
            as_inferred = [len(e["inferred"].get(n, ())) for n in sides] if field == guessed else []
            anchor = f"list-{fi}-{vi}"
            name = esc(value) + ("" if value in values else " (not in the list)")
            link = f'<a href="#{anchor}">{name}</a>' if e["rows"] else name
            table.append(f'<tr><td>{link}</td><td class="n">{len(e["papers"])}</td>' +
                         "".join(f'<td class="n">{c}</td>' for c in per_side) + f'<td class="n">{both}</td>' +
                         "".join(f'<td class="n">{c}</td>' for c in as_inferred) + "</tr>")
            if not e["rows"]:
                continue
            rows = sorted(e["rows"], key=lambda t: (t[1], t[0], t[2]["row"]))
            examples = "".join(
                f"<li><b>{esc(batch[pi]['label'])}</b>, {esc(side)} {n} row {x['row']}: {esc(row_place(reading, x))}; "
                f"{esc(spec['shown'])}: {esc(x[spec['shown']])}. <q>{esc(x[spec['quotes'][0]])}</q></li>"
                for pi, n, x in spread(rows))
            every = "".join(
                f"<tr><td>{esc(batch[pi]['label'])}</td><td class=\"n\">{n}</td><td class=\"n\">{x['row']}</td>"
                f"<td>{esc(row_place(reading, x))}</td><td>{esc(x[spec['shown']])}</td>"
                f"<td><q>{esc(x[spec['quotes'][0]])}</q></td></tr>"
                for pi, n, x in sorted(e["rows"], key=lambda t: (t[0], t[1], t[2]["row"])))
            blocks.append(f'<h4 id="{anchor}">{field}: {name}</h4><p class="muted">'
                          f'{counted(len(e["papers"]), "paper")}; {unit}: ' +
                          "; ".join(f"{side} {n} {c}" for n, c in zip(sides, per_side)) + f"; both {both}" +
                          ("; inferred: " + "; ".join(f"{side} {n} {c}" for n, c in zip(sides, as_inferred))
                           if as_inferred else "") + "</p>"
                          f'<ul class="examples">{examples}</ul>'
                          f'<details><summary>all {counted(len(e["rows"]), "row")}</summary>'
                          f'<div class="scroll"><table><tr><th>paper</th><th>{esc(side)}</th><th>row</th>'
                          f"<th>figure and panel</th><th>{esc(spec['shown'])}</th><th>quote</th></tr>{every}</table>"
                          "</div></details>")
        out.append(f'<h3>{esc(field)}</h3><div class="scroll"><table><tr><th>value</th><th>papers</th>' +
                   "".join(f"<th>{esc(unit)}, {esc(side)} {n}</th>" for n in sides) + f"<th>{esc(unit)}, both</th>" +
                   ("".join(f"<th>{esc(unit)} inferred, {esc(side)} {n}</th>" for n in sides)
                    if field == guessed else "") +
                   "</tr>" + "".join(table) + "</table></div>" + "".join(blocks))
    return "".join(out) + "</section>"


def review_reports(lib, reading, batch):
    """The readers' reports kept beside their rows, each in a closed section; NO_REPORTS where none is kept."""
    folder = figures_folder if reading == "figures" else pipeline_folder
    found = []
    for p in batch:
        for n in REVIEW[reading]["sides"]:
            path = os.path.join(folder(lib, p["key"], n), REPORT) if lib else ""
            if path and os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    found.append(f"<details><summary>{esc(p['label'])}, {esc(REVIEW[reading]['side'])} {n}</summary>"
                                 f"<pre class=\"report\">{esc(fh.read())}</pre></details>")
    return "".join(found) if found else f"<p>{esc(NO_REPORTS[reading])}</p>"


def review_grain(reading, batch, lib=None):
    spec = REVIEW[reading]
    side, sides = spec["side"], spec["sides"]
    heads = ["figures in the legends", "panels in the legends", "panels a figure, in the legends"]
    for n in sides:
        heads += [f"rows, {side} {n}", f"figures read, {side} {n}", f"rows a figure, {side} {n}"]
        heads += [f"panels read, {side} {n}"] if reading == "figures" else []
    table, details = [], []
    for p in batch:
        legs = p["legs"]
        n_figs, n_panels = len(legs), len(legend_units(legs))
        values = [n_figs, n_panels, n_panels / n_figs if n_figs else None]
        per_fig = {}
        for n in sides:
            if n not in p["sides"]:
                values += [None] * (4 if reading == "figures" else 3)
                continue
            rows = p["sides"][n]["rows"]
            figs = set().union(*(row_figures(reading, x) for x in rows))
            values += [len(rows), len(figs), len(rows) / len(figs) if figs else None]
            values += [len(set().union(*(x["units"] for x in rows)))] if reading == "figures" else []
            for x in rows:
                for fig in row_figures(reading, x):
                    per_fig.setdefault(fig, {}).setdefault(n, []).append(x)
        table.append((p, values))
        figs_cells = []
        for fig in sorted(set(legs) | set(per_fig), key=fig_order):
            cells = [len(legs[fig]["letters"]) or 1 if fig in legs else "-"]
            for n in sides:
                got = per_fig.get(fig, {}).get(n, [])
                cells.append(len(got) if n in p["sides"] else "-")
                if reading == "figures":
                    cells.append(len(set().union(*(x["units"] for x in got))) if n in p["sides"] else "-")
            figs_cells.append(f"<tr><td>{esc(figure_name(fig))}</td>" +
                              "".join(f'<td class="n">{c}</td>' for c in cells) + "</tr>")
        sub = ["panels in its legend"] + [h for n in sides for h in (
            [f"rows, {side} {n}"] + ([f"panels read, {side} {n}"] if reading == "figures" else []))]
        details.append(f"<details><summary>{esc(p['label'])}: by figure</summary><div class=\"scroll\"><table><tr>"
                       "<th>figure</th>" + "".join(f"<th>{esc(h)}</th>" for h in sub) + "</tr>" +
                       "".join(figs_cells) + "</table></div></details>")
    medians = [statistics.median(v) if v else None
               for v in ([vals[i] for _, vals in table if vals[i] is not None] for i in range(len(heads)))]

    def cell(v, med):
        if v is None:
            return '<td class="n">-</td>'
        ratio = f' <span class="muted">&times;{v / med:.2f}</span>' if med else ""
        return f'<td class="n">{shown_number(v)}{ratio}</td>'
    rows_html = "".join(f"<tr><td>{esc(p['label'])}</td>" + "".join(cell(v, m) for v, m in zip(vals, medians)) +
                        "</tr>" for p, vals in table)
    median_row = ("<tr><th>the batch's median</th>" +
                  "".join(f'<th class="n">{shown_number(m) if m is not None else "-"}</th>' for m in medians) + "</tr>")
    return ('<section id="grain"><h2>5. The grain</h2>'
            f"<p>Per paper, the legends' figures and panels, and per {side} the rows that pass their checks, the "
            f"figures they read and the rows a figure"
            + (", and the legends' panels they cover" if reading == "figures" else "") +
            "; beside each, its ratio to the batch's median.</p>"
            '<div class="scroll"><table><tr><th>paper</th>' + "".join(f"<th>{esc(h)}</th>" for h in heads) + "</tr>" +
            rows_html + median_row + "</table></div>" + "".join(details) +
            f"<h3>The readers' hard calls</h3>{review_reports(lib, reading, batch)}</section>")


def review_two_rows(reading, batch):
    spec = REVIEW[reading]
    side = spec["side"]
    out = ['<section id="two-rows"><h2>6. Two rows per paper, beside their text</h2>',
           f"<p>From the {side} the paper's check names (else {side} {spec['sides'][0]}), by a fixed rule: the first "
           "row of the first main figure and the first row of the last; with one main figure, its first row and its "
           "last. Beside each: the legend's text for its panels and the Results sentences that cite its figure, each "
           "quote marked where it is found as written.</p>"]
    for p in batch:
        n = p["kept"] if p["kept"] in p["sides"] else min(p["sides"])
        out.append(f"<h3>{esc(p['label'])}</h3>")
        rows = p["sides"][n]["rows"]
        if not rows:
            out.append(f'<p class="muted">No row of {esc(side)} {n} passes its checks.</p>')
        for x in two_rows(reading, rows):
            quotes = [x[q] for q in spec["quotes"]]
            place = figure_place(x) if reading == "figures" else EXTENDED_DATA.sub("Extended Data Fig. ", x["panel"])
            legend = legend_texts(p["legs"], row_panels(reading, x))
            cited = [s.strip() for s in sentences_with(p["results"], place, 2)]
            shown = " ".join([" ".join([e["title"]] + e["parts"]) for e in legend] + cited)
            text = ["<p><b>The legend</b></p>", legend_html(legend, quotes),
                    "<p><b>The Results sentences that cite its figure, those that cite the panel first</b></p>"]
            text += [f'<div class="said">{marked(s, quotes)}</div>' for s in cited] or [
                '<p class="muted">None found.</p>']
            for name in spec["quotes"]:
                if normal(x[name]) in normal(shown):
                    continue
                s = quote_sentence(x[name], p["whole"])
                text.append(f"<p><b>The sentence the {esc(name.replace('_', ' '))} is from</b></p>" +
                            (f'<div class="said">{marked(s, quotes)}</div>' if s else
                             '<p class="muted">It runs over more than two sentences.</p>'))
            out.append(f'<div class="case"><h4>{esc(side)} {n}, row {x["row"]}: {esc(row_place(reading, x))}</h4>'
                       f'<div class="pair"><div>{review_row(reading, x, f"{side} {n}")}</div>'
                       f'<div>{"".join(text)}</div></div></div>')
    return "".join(out) + "</section>"


def review_footer(lib, reading, command, written, labels=None):
    spec = REVIEW[reading]
    lists = review_lists(lib, reading, labels)
    hashes = [(f"the brief, agents/{b}", file_sha256(os.path.join(PLUGIN_FOLDER, "agents", b))) for b in spec["briefs"]]
    hashes.append((f"the closed lists ({', '.join(lists)}), as papers.py holds them, in JSON",
                   hashlib.sha256(json.dumps(lists, ensure_ascii=False).encode("utf-8")).hexdigest()))
    if reading == "moves":
        hashes.append(("the module list, module_list.md", file_sha256(os.path.join(lib, MODULE_LIST))))
    hashes.append(("the checks, papers.py", file_sha256(os.path.abspath(__file__))))
    when = (f'<time datetime="{time.strftime("%Y-%m-%dT%H:%M:%SZ", written)}">'
            f'{time.strftime("%Y-%m-%d %H:%M UTC", written)}</time>')
    return ('<footer id="footer"><h2>7. How this page was written</h2><table>'
            f"<tr><th>the command</th><td><code>{esc(command)}</code></td></tr>"
            f"<tr><th>the library</th><td><code>{esc(lib)}</code></td></tr>"
            f"<tr><th>written</th><td>{when}</td></tr>" +
            "".join(f"<tr><th>sha256 of {esc(what)}</th><td><code>{sha}</code></td></tr>" for what, sha in hashes) +
            "</table><p>This page holds quotes from the papers. It stays in the library, which is the user's own; "
            "keep it out of any public place.</p></footer>")


def review_page(lib, reading, keys=None, out="", written=None):
    """(the page, the batch) of a reading's calibration review: the papers named, or every paper whose reading has an
    agreement file or a check."""
    rows = papers(lib)
    if keys:
        chosen = [paper(rows, k) for k in keys]
    else:
        chosen = [r for r in rows if r["state"] != "kept out" and reviewed(lib, r["key"], reading)]
    if not chosen:
        raise LibraryError(f"no paper of {lib} has its {reading} reading checked or agreed")
    batch = [review_paper(lib, r, reading) for r in chosen]
    labels = batch_labels(reading, batch)
    lists = review_lists(lib, reading, labels)
    command = (f"papers.py review {lib} --reading {reading}" + (f" --keys {','.join(keys)}" if keys else "") +
               f" --out {out}")
    title = f"Calibration review: the {reading} reading"
    nav = "".join(f'<a href="#{a}">{t}</a>' for a, t in (
        ("papers", "1. The papers"), ("disagreements", "2. Disagreements"), ("other", "3. other (...)"),
        ("lists", "4. The closed lists"), ("grain", "5. The grain"), ("two-rows", "6. Two rows per paper"),
        ("footer", "7. How it was written")))
    page = "\n".join([
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">', f"<title>{esc(title)}</title>",
        f"<style>{REVIEW_STYLE}</style>", "</head>", "<body>", "<main>",
        f"<h1>{esc(title)}</h1><p class=\"muted\">{counted(len(batch), 'paper')} of the library "
        f"<code>{esc(lib)}</code>. The page holds quotes from the papers: it stays in the library.</p>",
        f"<nav>{nav}</nav>",
        review_header(reading, batch), review_disagreements(reading, batch), review_other(reading, batch),
        review_closed_lists(reading, batch, lists), review_grain(reading, batch, lib), review_two_rows(reading, batch),
        review_footer(lib, reading, command, written or time.gmtime(), labels), "</main>", "</body>", "</html>"]) + "\n"
    return page, batch


REVIEW_TITLE = re.compile(r"<title>Calibration review: the [a-z]+ reading</title>")


def review_target(lib, out):
    """The page's file, its links resolved: an .html file inside the library, outside its working folders, and either
    new or an earlier review page, so that the page never takes the place of the library's own files."""
    root, target = os.path.realpath(lib), os.path.realpath(out)
    try:
        inside = os.path.commonpath([root, target]) == root and target != root
    except ValueError:  # another drive
        inside = False
    if not inside:
        raise LibraryError(f"the page holds quotes from the papers, so it is written inside the library ({lib}), not "
                           f"at {out}")
    if not target.lower().endswith(".html") or os.path.isdir(target):
        raise LibraryError(f"the page is an .html file; {out} is not one")
    first = os.path.relpath(target, root).split(os.sep)[0]
    if os.path.normcase(first) in {os.path.normcase(f) for f in FOLDERS}:
        raise LibraryError(f"{out} is in the library's {first}/ folder, which holds its own files; write the page "
                           "beside them")
    if os.path.exists(target):
        with open(target, encoding="utf-8", errors="replace") as fh:
            earlier = fh.read()
        if not (earlier.startswith("<!doctype html>") and REVIEW_TITLE.search(earlier)):
            raise LibraryError(f"{out} is there already and is not an earlier review page; name another file")
    return target


def review(lib, reading, keys=None, out=None, written=None):
    """The review page of a reading's calibration batch, written inside the library, since it holds the papers'
    quotes."""
    if not out:
        raise LibraryError("name the page's file with --out, inside the library")
    target = review_target(lib, out)
    page, batch = review_page(lib, reading, keys, out, written)
    write_text(target, page)
    side = REVIEW[reading]["side"]
    return [f"{counted(len(batch), 'paper')}; {counted(sum(len(p['differ']) for p in batch), 'place')} where two "
            f"{side}s disagree",
            f"written to {out}; it holds quotes from the papers: keep it in the library, out of any public place"]


# -- the catalogue ----------------------------------------------------------------------------------------------------

TABLES = {"moves": ("moves.tsv", ("key",) + FIELDS["moves"] + ("looks", "version")),
          "chain": ("chain_steps.tsv", ("key", "paper") + FIELDS["chain"] + ("looks", "version")),
          "methods": ("methods.tsv", ("key",) + FIELDS["methods"] + ("looks", "version")),
          "code": ("code.tsv", ("key", "repository", "commit") + FIELDS["code"] + ("looks", "version")),
          "figures": ("figures.tsv", ("key",) + FIELDS["figures"] + ("looks", "version"))}


def merge(lib):
    rows = papers(lib)
    tables = {reading: [] for reading in READINGS}
    chain_papers, out = [], []
    for r in rows:
        key = r["key"]
        check_path = os.path.join(lib, "checks", f"{key}.json")
        if r["state"] not in ("checked", "merged") or not os.path.exists(check_path):
            continue
        with open(check_path, encoding="utf-8") as fh:
            report = json.load(fh)
        for reading, rep in report.items():
            path = os.path.join(lib, *rep.get("file", f"rows/{key}/{reading}.md").split("/"))
            written = read_text(path) if os.path.exists(path) else ""
            if hashlib.sha1(written.encode("utf-8")).hexdigest() != rep["sha1"]:
                out.append(f"{key} {reading}: its rows changed after the check; check it again")
                continue
            if rep["whole"]:
                out.append(f"{key} {reading}: left out, {'; '.join(rep['whole'])}")
                continue
            version = fixed_version(reading, rep.get("version"))
            if VERSIONS.get(reading) and not version:
                out.append(f"{key} {reading}: left out, not read under a fixed version of the reading "
                           f"({', '.join(VERSIONS[reading])})")
                continue
            head = field_lines(written)
            passed = [x for x in rep["rows"] if not x["fails"]]
            if reading == "figures":  # a figures row by its version's fields; the pilot's are never merged
                other = any(figure_names(x["row"]) is FIGURES_FIRST_VERSION for x in passed)
            else:
                other = any(len(x["row"]) != len(FIELDS[reading]) for x in passed)
            if other:
                out.append(f"{key} {reading}: left out, checked as another version of the reading; check it again")
                continue
            left_out = conclusions_left_out(rep) if reading == "figures" else {}
            for x in passed:
                fields = dict(zip(figure_names(x["row"]) if reading == "figures" else FIELDS[reading], x["row"]))
                looks = list(x["looks"])
                if reading == "figures":  # a figure's failed conclusion stays with its rows, to look at
                    name = figure_name(figures(fields["figure"])[0])
                    concl = rep.get("conclusions", {}).get(name, {})
                    looks += [f"the figure's conclusion: {why}" for why in concl.get("fails", [])]
                    if name in left_out:  # the figure's conclusion, from a row that failed, on its first row merged
                        at, fields["conclusion_quote"] = left_out.pop(name)
                        looks.append(f"the figure's conclusion is from its row {at}, which failed its checks")
                fields.update(key=key, looks="; ".join(looks), version=version or "")
                if reading == "chain":
                    fields["paper"] = r["label"]
                if reading == "code":
                    fields.update(repository=head.get("repository", ""), commit=head.get("commit", ""))
                tables[reading].append(fields)
            if reading == "chain":
                chain_papers.append({"key": key, **{f: head.get(f, "") for f in CHAIN_FIELDS}})
            out.append(f"{key} {reading}: {len(passed)} of {len(rep['rows'])} rows merged")
            r["state"] = "merged"
    record_changes(lib, tables)
    for reading, (name, columns) in TABLES.items():
        buf = io.StringIO()
        w = csv.DictWriter(buf, columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(tables[reading])
        write_text(os.path.join(lib, "catalogue", name), buf.getvalue())
    buf = io.StringIO()
    w = csv.DictWriter(buf, ("key",) + CHAIN_FIELDS, delimiter="\t", lineterminator="\n")
    w.writeheader()
    w.writerows(chain_papers)
    write_text(os.path.join(lib, "catalogue", "chain_papers.tsv"), buf.getvalue())
    save_papers(lib, rows)
    return out or ["nothing checked to merge"]


CHANGES = "changes.tsv"  # in the catalogue: what each merge changed, one line a paper and reading
CHANGE_FIELDS = ("date", "reading", "key", "rows_before", "rows_now", "versions")


def rows_by_paper(rows):
    """{key: (rows, sha1 of their fields)} of a reading's rows in the catalogue."""
    by = {}
    for x in rows:
        by.setdefault(x["key"], []).append(x)
    return {k: (len(v), hashlib.sha1(json.dumps(v, sort_keys=True).encode("utf-8")).hexdigest())
            for k, v in by.items()}


def record_changes(lib, tables):
    """The papers whose rows of a reading a merge changes, added to catalogue/changes.tsv with the rows before and now
    and the versions they were read under."""
    changes, today = [], time.strftime("%Y-%m-%d")
    for reading, (_, columns) in TABLES.items():
        before = rows_by_paper([{c: x.get(c, "") for c in columns} for x in catalogue_rows(lib, reading)])
        now = rows_by_paper([{c: x.get(c, "") for c in columns} for x in tables[reading]])
        for key in sorted(set(before) | set(now)):
            if before.get(key, (0, None))[1] != now.get(key, (0, None))[1]:
                versions = sorted({x.get("version", "") for x in tables[reading] if x["key"] == key} - {""})
                changes.append({"date": today, "reading": reading, "key": key, "rows_before": before.get(key, (0,))[0],
                                "rows_now": now.get(key, (0,))[0], "versions": ", ".join(versions)})
    if not changes:
        return
    path = os.path.join(lib, "catalogue", CHANGES)
    buf = io.StringIO()
    w = csv.DictWriter(buf, CHANGE_FIELDS, delimiter="\t", lineterminator="\n")
    if not os.path.exists(path):
        w.writeheader()
    w.writerows(changes)
    write_text(path, (read_text(path) if os.path.exists(path) else "") + buf.getvalue())


def conclusions_left_out(rep):
    """{figure: (row number, conclusion)} of the figures whose one conclusion is on a row that failed its checks, from
    a paper's figures check."""
    out = {}
    for i, x in enumerate(rep["rows"], 1):
        names = figure_names(x["row"])
        if not x["fails"] or names is FIGURES_FIRST_VERSION or len(x["row"]) != len(names):
            continue
        fields = dict(zip(names, x["row"]))
        named = figures(fields["figure"])
        if len(named) != 1:
            continue
        concl = rep.get("conclusions", {}).get(figure_name(named[0]), {})
        if concl.get("quote") is not None and fields["conclusion_quote"] == concl["quote"]:
            out.setdefault(figure_name(named[0]), (i, concl["quote"]))
    return out


def catalogue(lib, reading):
    name, _ = TABLES[reading]
    path = os.path.join(lib, "catalogue", name)
    if not os.path.exists(path):
        raise LibraryError("the catalogue is empty; merge first")
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def count(lib, reading, by=None):
    """Papers and rows per value of the fields named, any other (...) as other; moves are counted by module unless
    other fields are named; figures also by the panels their rows cover, a row of panels b-c counting two, and a chart
    without " (inferred)", with the rows that give it as inferred."""
    by = by or COUNT_BY.get(reading)
    if not by:
        raise LibraryError(f"name the field to count {reading} by with --by")
    rows = catalogue(lib, reading)
    if rows and any(b not in rows[0] for b in by):
        raise LibraryError(f"the fields of {reading} are {', '.join(rows[0])}")
    as_labels = COUNT_AS_LABELS.get(reading, ())
    charts = reading == "figures" and "chart" in by
    groups = {}
    for x in rows:
        value = tuple(label_of(chart_as_listed(x[b]) if charts and b == "chart" else x[b]) if b in as_labels else
                      x[b] for b in by)
        g = groups.setdefault(value, [set(), 0, set(), 0])
        g[0].add(x["key"])
        g[1] += 1
        if reading == "figures":  # a panel by the figure read, however the figure is written
            g[2] |= {(x["key"], tuple(figures(x["figure"])), y) for y in row_letters(x["panel"]) or ()}
        g[3] += charts and is_inferred(x["chart"])
    panels = reading == "figures"
    out = [f"| {' | '.join(by)} | papers | rows |" + (" panels |" if panels else "") +
           (" rows inferred |" if charts else ""),
           "|" + "---|" * (len(by) + 2 + panels + charts)]
    for value, (keys, n, units, guessed) in sorted(groups.items(), key=lambda kv: (-len(kv[1][0]), kv[0])):
        out.append(f"| {' | '.join(value)} | {len(keys)} | {n} |" + (f" {len(units)} |" if panels else "") +
                   (f" {guessed} |" if charts else ""))
    return out + [f"{len({x['key'] for x in rows})} papers in all"]


# -- batches ----------------------------------------------------------------------------------------------------------

def batch_path(lib, name):
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*", name):
        raise LibraryError(f"a batch's name is letters, digits, '.', '_' and '-': {name!r}")
    return os.path.join(lib, "batches", f"{name}.json")


def plan_batch(lib, name, reading, keys):
    """A batch planned before it runs (ADR 0033, amendment 1): its papers in the order they are read, those read
    twice (the first of every five: the drift check), the version it is read under, its estimate in tokens and the
    limit it stops before passing."""
    if reading not in COST:
        raise LibraryError(f"a batch is of the {' or the '.join(COST)} reading")
    path = batch_path(lib, name)
    if os.path.exists(path):
        raise LibraryError(f"batch {name} is planned already: {path}")
    if not keys:
        raise LibraryError("name the batch's papers with --keys")
    if len(set(keys)) != len(keys):
        raise LibraryError("a paper is named twice")
    rows = papers(lib)
    v = version_of(reading)
    if VERSIONS.get(reading) and not v["version"]:
        raise LibraryError(f"the {reading} reading's briefs or lists are not one of its fixed versions "
                           f"({', '.join(VERSIONS[reading])}): record the new version before a batch")
    plan = [{"key": k, "label": paper(rows, k)["label"], "readings": [1, 2] if i % TWICE_EVERY == 0 else [1]}
            for i, k in enumerate(keys)]
    readings = sum(len(x["readings"]) for x in plan)
    estimate = readings * COST[reading]
    data = {"name": name, "reading": reading, "version": v["version"], "sha256": v["sha256"],
            "planned": time.strftime("%Y-%m-%d"), "papers": plan, "estimate": estimate,
            "limit": int(estimate * OVER), "spent": []}
    write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    twice = [short_label(x["label"]) for x in plan if len(x["readings"]) > 1]
    return [f"batch {name}: {counted(len(plan), 'paper')}, {readings} "
            f"{'pipelines' if reading == 'moves' else 'readings'} of the {reading} reading, under "
            f"{v['version'] or 'no fixed version'}",
            f"read twice, for the drift check: {and_list(twice)}",
            f"estimate {estimate:,} tokens; the batch stops before it would pass {data['limit']:,} ({OVER} times)",
            f"written to {path}"]


def record_spent(lib, name, key, n, tokens):
    """The tokens one reading of a batch took, recorded; the batch's total against its estimate, and whether the next
    reading may run."""
    path = batch_path(lib, name)
    if not os.path.exists(path):
        raise LibraryError(f"no batch {name}; plan it with papers.py batch")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    planned = next((x for x in data["papers"] if x["key"] == key), None)
    if not planned or n not in planned["readings"]:
        raise LibraryError(f"{key}'s reading {n} is not in batch {name}")
    if any(s["key"] == key and s["n"] == n for s in data["spent"]):
        raise LibraryError(f"{key}'s reading {n} is recorded already")
    if tokens <= 0:
        raise LibraryError("the tokens are a positive number")
    data["spent"].append({"key": key, "n": n, "tokens": tokens})
    write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    total = sum(s["tokens"] for s in data["spent"])
    done, all_readings = len(data["spent"]), sum(len(x["readings"]) for x in data["papers"])
    cost = COST[data["reading"]]
    out = [f"batch {name}: {done} of {all_readings} done, {total:,} tokens; estimated {done * cost:,} for them and "
           f"{data['estimate']:,} for the batch"]
    if done < all_readings and total + cost > data["limit"]:
        out.append(f"stop: the next reading would take the batch past {data['limit']:,} tokens ({OVER} times its "
                   "estimate); ask the user before it runs")
    elif done == all_readings:
        out.append("the batch is done" + (f", past its limit of {data['limit']:,}" if total > data["limit"] else ""))
    return out


# -- the parts of the knowledge base written from the catalogue -------------------------------------------------------

PART_START = re.compile(r"^<!-- from the papers: ([a-z ]+) -->[ \t]*\n", re.M)
PART_END = "<!-- end of the part from the papers -->"
PART_NOTE = "Written by `papers.py write-base` from the library's catalogue; not edited by hand."


def short_label(label):
    """A paper's label without the ID it begins with: "Belk 2022" for "PMID35750052 Belk 2022"."""
    return re.sub(r"^(?:PMID|PMC)\d+\s+", "", label)


def catalogue_rows(lib, reading):
    """A reading's rows in the catalogue, none where it has no table yet."""
    try:
        return catalogue(lib, reading)
    except LibraryError:
        return []


def cell(value):
    return value.replace("|", "\\|")


def part_papers_read(lib):
    """The papers whose rows are in the catalogue, with the readings and versions they were read for."""
    known = {r["key"]: r for r in papers(lib)}
    read = {}
    for reading in READINGS:
        for x in catalogue_rows(lib, reading):
            read.setdefault(x["key"], {}).setdefault(reading, set()).add(x.get("version", ""))
    lines = [PART_NOTE, "", f"{counted(len(read), 'paper')} read:", "", "| Paper | DOI | Read for |", "|---|---|---|"]
    for key in sorted(read, key=lambda k: (short_label(known.get(k, {}).get("label", k)), k)):
        r = known.get(key, {})
        what = "; ".join(reading + (f" ({', '.join(sorted(v for v in versions if v))})" if any(versions) else "")
                         for reading, versions in sorted(read[key].items(), key=lambda kv: READINGS.index(kv[0])))
        lines.append(f"| {cell(short_label(r.get('label', key)))} | {cell(r.get('doi') or 'none')} | {what} |")
    return lines


def part_figures_by_evidence(lib):
    """For each kind of evidence, the papers whose figures show it, its panels, the charts the papers drew it with (a
    chart inferred counted as its chart) and example panels, from the rows read under a fixed version."""
    labels = {r["key"]: short_label(r["label"]) for r in papers(lib)}
    rows = [x for x in catalogue_rows(lib, "figures") if x.get("version") in VERSIONS.get("figures", {})]
    groups = {}
    for x in rows:
        kind = label_of(x["evidence"])
        g = groups.setdefault(kind, {"papers": set(), "panels": set(), "charts": {}, "rows": [], "words": {}})
        g["papers"].add(x["key"])
        g["panels"] |= {(x["key"], tuple(figures(x["figure"])), y) for y in row_letters(x["panel"]) or ()}
        g["charts"].setdefault(label_of(chart_as_listed(x["chart"])), set()).add(x["key"])
        g["rows"].append(x)
        if kind == "other":
            words = re.fullmatch(r"other \((.*)\)", x["evidence"])
            g["words"].setdefault(words.group(1) if words else "no words", set()).add(x["key"])
    versions = sorted({x["version"] for x in rows})
    lines = [PART_NOTE, "",
             f"Of the {counted(len({x['key'] for x in rows}), 'paper')} read for their figures"
             f"{' (' + ', '.join(versions) + ')' if versions else ''}: the panels by what they show, the charts the "
             "papers drew them with (in papers) and examples.", "",
             "| Evidence | Papers | Panels | Charts (papers) | Examples |", "|---|---|---|---|---|"]
    by_papers = lambda kv: (-len(kv[1]), kv[0])
    for kind in FIGURE_EVIDENCE:
        g = groups.get(kind)
        if not g:
            continue
        charts = ", ".join(f"{c} ({len(k)})" for c, k in sorted(g["charts"].items(), key=by_papers))
        if kind == "other":
            kind = "other: " + "; ".join(f"{w} ({len(k)})" for w, k in sorted(g["words"].items(), key=by_papers))
        examples = "; ".join(f"{labels.get(x['key'], x['key'])}, {figure_place(x)}"
                             for _, x in spread([(x["key"], x) for x in g["rows"]]))
        lines.append(f"| {cell(kind)} | {len(g['papers'])} | {len(g['panels'])} | {cell(charts)} | {cell(examples)} |")
    return lines


PARTS = {"papers read": part_papers_read, "figures by evidence": part_figures_by_evidence}


def write_base(lib, knowledge_base=None, check_only=False):
    """The parts of the knowledge base that code writes from the catalogue (ADR 0033, amendment 1): in each of the
    base's .md files, between `<!-- from the papers: <part> -->` and `<!-- end of the part from the papers -->`, the
    part as the catalogue gives it now; nothing else in the base is touched, and nothing is written unless every
    file's markers can be read. With check_only, nothing is written, and the parts that differ from the catalogue are
    named."""
    kb = knowledge_base or settings(lib).get("knowledge_base")
    if not kb or not os.path.isdir(kb):
        raise LibraryError("no knowledge base folder; name it with --knowledge-base, or give it at init")
    made, planned = {}, []
    for folder, dirs, files in os.walk(kb):
        dirs.sort()
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            path = os.path.join(folder, f)
            rel = os.path.relpath(path, kb).replace(os.sep, "/")
            text = read_text(path)
            pieces, names, at = [], [], 0
            for m in PART_START.finditer(text):
                if m.start() < at:
                    raise LibraryError(f"{rel}: a part from the papers begins inside another")
                name, end = m.group(1), text.find(PART_END, m.end())
                if end < 0:
                    raise LibraryError(f"{rel}: the part {name!r} has no line {PART_END}")
                if name not in PARTS:
                    raise LibraryError(f"{rel}: no part {name!r}; the parts are {and_list(list(PARTS))}")
                if name not in made:
                    made[name] = "\n".join(PARTS[name](lib)) + "\n"
                pieces.append(text[at:m.end()] + made[name])
                names.append(name)
                at = end
            if names:
                planned.append((path, rel, text, "".join(pieces) + text[at:], names))
    out = []
    for path, rel, text, new, names in planned:
        if new == text:
            out.append(f"{rel}: {and_list(names)}, as the catalogue gives them")
        elif check_only:
            out.append(f"{rel}: {and_list(names)}, not as the catalogue gives them; write them with "
                       "papers.py write-base")
        else:
            write_text(path, new)
            out.append(f"{rel}: {and_list(names)}, written")
    return out or [f"no part from the papers in {kb}: mark where one goes with <!-- from the papers: <part> --> and "
                   f"{PART_END} (the parts: {and_list(list(PARTS))})"]


# -- drafts -----------------------------------------------------------------------------------------------------------

CITATION = re.compile(r"\[([^\[\]]+)\]")
READING_MARK = "pertura's reading"  # a sentence of Pertura's own, in the one field the card form gives it


FIGURE_NAME = re.compile(r"(?:\b(?:extended\s+data|expanded\s+view|ed|ev|supplementary|supplemental|supp\.?)\s+)?"
                         r"\bfig(?:ure)?s?\.?\s*(?:s|e[dv])?\s?\d+(?!\d)", re.I)


def cited_panels(where):
    """[(figure, letters)] of a draft's citation of figures' panels ("Fig. 1b-c", "Extended Data Fig. 3 a", "Fig. 2"
    for the whole figure, as {"all"}), the letters read as the checks read a figures row's panel; None if they cannot
    be read so."""
    s = where.strip().rstrip(".")
    starts = [m.start() for m in FIGURE_NAME.finditer(s)]
    if not starts or starts[0] != 0:
        return None
    out = []
    for a, b in zip(starts, starts[1:] + [len(s)]):
        piece = re.sub(r"(?:\s*(?:[,;&]|\band\b))+\s*$", "", s[a:b].strip())
        name = FIGURE_NAME.match(piece)
        named, rest = figures(name.group(0)), piece[name.end():].strip()
        letters = row_letters(rest) if rest else {"all"}
        if len(named) != 1 or letters is None:
            return None
        out.append((named[0], letters))
    return out


def panels_covered(where, units):
    """Whether merged figures rows, {(figure, letter or `all`)}, cover every panel a citation names; a figure cited
    whole is covered by any of its rows."""
    cited = cited_panels(where)
    if not cited:
        return False
    for fig, letters in cited:
        if letters == {"all"}:
            if not any(u[0] == fig for u in units):
                return False
        elif not all((fig, y) in units or (fig, "all") in units for y in letters):
            return False
    return True


def check_draft(lib, draft):
    """Each sentence of a draft has its sources, and each source is a row of the catalogue."""
    kept = KeptOut.of(lib)
    labels = {r["label"]: r["key"] for r in papers(lib)}
    known = {}  # label -> its merged rows' figures (moves, chain), panels (figures), sections and code lines
    for reading in READINGS:
        try:
            rows = catalogue(lib, reading)
        except LibraryError:
            continue
        for x in rows:
            lab = x.get("paper", "")
            k = known.setdefault(lab, {"figures": set(), "sections": set(), "code": set(), "panels": set(),
                                       "units": set()})
            place = figure_place(x) if reading == "figures" else x.get("panel", "") or x.get("figure", "")
            if reading == "figures":
                k["units"] |= {(fig, y) for fig in figures(x["figure"]) for y in row_letters(x["panel"]) or ()}
            else:
                k["figures"].update(figures(place))
            k["panels"].add(normal(place))
            k["sections"].update(normal(part) for part in sections_of(x.get("section", "")))
            if reading == "code":
                k["code"].add(f"{x['file']}:{x['line']}")
    text = read_text(draft)
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    out, notes, blocks, cur, cur_field, field = [], [], [], [], None, None
    for line in text.split("\n"):  # blocks of text, each with the card field it sits in
        s = line.strip()
        if not s or s.startswith("#") or re.fullmatch(r"[|\s:-]+", s) or s.startswith("<!--"):
            if cur:
                blocks.append((cur_field, " ".join(cur)))
            cur = []
            field = None if s.startswith("#") else field
        elif re.match(r"^([-*]|\d+\.)\s", s):
            if cur:
                blocks.append((cur_field, " ".join(cur)))
            item = re.sub(r"^([-*]|\d+\.)\s+", "", s)
            named = re.match(r"\*\*([^*]+?):\*\*", item)
            if named and not line[:1].isspace():
                field = named.group(1).strip().lower()
            cur, cur_field = [item], field
        else:
            cur_field = cur_field if cur else field
            cur.append(s)
    if cur:
        blocks.append((cur_field, " ".join(cur)))
    for field, block in blocks:
        if re.match(r"^[-*]?\s*\*\*source", block, re.I) or re.fullmatch(r"\*\*[^*]+:\*\*", block.strip()):
            continue  # the card's own source field, or a field's name above its items
        cites = []
        marked = CITATION.sub(lambda m: cites.append(m.group(1)) or f"\0{len(cites) - 1}\0", block)
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z*\[(])", marked):
            if not re.search(r"[A-Za-z]{3}", sentence.replace("\0", "")):
                continue
            shown = re.sub("\0" + r"\d+" + "\0", "[...]", sentence.strip())
            if not re.search(r"\0\d+\0[\s.!?)*]*$", sentence.strip()):
                out.append(f"no source: {shown[:160]}")
            marks = re.findall("\0" + r"(\d+)" + "\0", sentence)
            if any(cites[int(i)].strip().lower() == READING_MARK for i in marks):
                if field == "stop or turn when":
                    notes.append(f"Pertura's reading, for the reviewer: {shown[:160]}")
                else:
                    out.append(f"Pertura's reading outside the field the form gives it ({field}): {shown[:160]}")
        for cite in cites:
            if cite.strip().lower() == READING_MARK:
                continue
            for source in cite.split(";"):
                out += [f"cites a source kept out: {c}" for c in kept.cited_in(source)]
                lab = next((x for x in sorted(labels, key=len, reverse=True) if source.strip().startswith(x)), "")
                if not lab:
                    if re.match(r"\s*(PMID|PMC|DOI|pdf-)", source):
                        out.append(f"not a paper of the library: [{source.strip()}]")
                    continue
                where = source.strip()[len(lab):].strip(" ,")
                k = known.get(lab)
                if k is None:
                    out.append(f"no merged row of {lab}: [{source.strip()}]")
                elif normal(where) in k["panels"]:
                    continue  # the panel exactly as a row gives it
                elif where.startswith("code "):
                    if where[5:].strip() not in k["code"]:
                        out.append(f"no code row at {where[5:].strip()}: [{source.strip()}]")
                elif figures(where):
                    if not (set(figures(where)) & k["figures"] or panels_covered(where, k["units"])):
                        out.append(f"no row of {lab} names {where}: [{source.strip()}]")
                elif where and normal(where) not in k["sections"]:
                    out.append(f"no row of {lab} has {where!r}: [{source.strip()}]")
    return (out or ["every sentence has its sources, and each source is a row of the catalogue"]) + notes


# -- the command line -------------------------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="papers.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("init")
    a.add_argument("library")
    a.add_argument("--exclusions")
    a.add_argument("--knowledge-base")
    a = sub.add_parser("register")
    a.add_argument("library")
    a.add_argument("ids", nargs="*")
    a.add_argument("--pdf")
    a.add_argument("--label")
    a.add_argument("--doi")
    a = sub.add_parser("fetch")
    a.add_argument("library")
    a.add_argument("keys", nargs="*")
    a.add_argument("--list", action="store_true")
    a.add_argument("--code")
    a.add_argument("--commit")
    a.add_argument("--figure-images", action="store_true", help="the main figures' images, for the figures reading")
    for name in ("cut", "check", "agree"):
        a = sub.add_parser(name)
        a.add_argument("library")
        a.add_argument("key")
        if name == "check":
            a.add_argument("--pipeline", type=int, choices=PIPELINES)
            a.add_argument("--figures", type=int, choices=FIGURE_READINGS, dest="figure_reading")
        if name == "agree":
            a.add_argument("--reading", choices=("moves", "figures"), default="moves")
    a = sub.add_parser("modules")
    a.add_argument("library")
    a.add_argument("--knowledge-base")
    a = sub.add_parser("show")
    a.add_argument("library")
    a.add_argument("key")
    a.add_argument("--rows", type=int, default=2)
    a = sub.add_parser("merge")
    a.add_argument("library")
    a = sub.add_parser("count")
    a.add_argument("library")
    a.add_argument("reading", choices=READINGS)
    a.add_argument("--by", action="append")
    a = sub.add_parser("review", description=(
        "Writes one static HTML page for the review of a reading's calibration batch: the papers and their measures, "
        "where each paper's two readings disagree, the rows under other (...), each closed list with its counts and "
        "examples, the grain, and two rows per paper beside their text. The page holds quotes from the papers, so it "
        "is written inside the library folder, which stays the user's own: keep it out of any public place."))
    a.add_argument("library")
    a.add_argument("--reading", choices=tuple(REVIEW), required=True)
    a.add_argument("--keys", help="the papers' keys, separated by commas; when not given, every paper whose reading "
                                  "has an agreement file or a check")
    a.add_argument("--out", required=True, help="the page's .html file, inside the library and outside its folders, "
                                                "since it holds quotes from the papers")
    a = sub.add_parser("check-draft")
    a.add_argument("library")
    a.add_argument("draft")
    a = sub.add_parser("start")
    a.add_argument("library")
    a.add_argument("key")
    a.add_argument("--reading", choices=READINGS, required=True)
    a.add_argument("--pipeline", type=int, choices=PIPELINES)
    a.add_argument("--figures", type=int, choices=FIGURE_READINGS, dest="figure_reading")
    a = sub.add_parser("batch")
    a.add_argument("library")
    a.add_argument("name")
    a.add_argument("--reading", choices=tuple(COST), required=True)
    a.add_argument("--keys", required=True, help="the papers' keys, separated by commas, in the order they are read")
    a = sub.add_parser("spent")
    a.add_argument("library")
    a.add_argument("batch")
    a.add_argument("key")
    a.add_argument("n", type=int, help="the reading's number (the moves' pipeline)")
    a.add_argument("tokens", type=int)
    a = sub.add_parser("write-base")
    a.add_argument("library")
    a.add_argument("--knowledge-base")
    a.add_argument("--check", action="store_true", help="write nothing; name the parts that differ from the catalogue")
    args = p.parse_args(argv)
    lib = args.library
    try:
        if args.command == "init":
            out = [init(lib, args.exclusions, args.knowledge_base)]
        elif args.command == "register":
            if not args.ids and not args.pdf:
                raise LibraryError("name papers by DOI, PubMed ID or PMC ID, or give --pdf")
            out = register(lib, args.ids, args.pdf, args.label, args.doi)
        elif args.command == "fetch":
            if args.list:
                out = fetch_list(lib)
            elif not args.keys:
                raise LibraryError("name the papers' keys, or use --list")
            else:
                out = fetch(lib, args.keys, args.code, args.commit, args.figure_images)
        elif args.command == "cut":
            out = [cut(lib, args.key)]
        elif args.command == "modules":
            out = [write_module_list(lib, args.knowledge_base)]
        elif args.command == "agree":
            out = agree_figures(lib, args.key) if args.reading == "figures" else agree(lib, args.key)
        elif args.command == "check":
            out = check(lib, args.key, args.pipeline, args.figure_reading)
        elif args.command == "show":
            out = show(lib, args.key, args.rows)
        elif args.command == "merge":
            out = merge(lib)
        elif args.command == "count":
            out = count(lib, args.reading, args.by)
        elif args.command == "review":
            keys = [k.strip() for k in args.keys.split(",") if k.strip()] if args.keys else None
            out = review(lib, args.reading, keys, args.out)
        elif args.command == "start":
            if args.pipeline and args.figure_reading:
                raise LibraryError("name a pipeline or a figures reading, not both")
            out = start(lib, args.key, args.reading, args.pipeline or args.figure_reading)
        elif args.command == "batch":
            out = plan_batch(lib, args.name, args.reading, [k.strip() for k in args.keys.split(",") if k.strip()])
        elif args.command == "spent":
            out = record_spent(lib, args.batch, args.key, args.n, args.tokens)
        elif args.command == "write-base":
            out = write_base(lib, args.knowledge_base, args.check)
        else:
            out = check_draft(lib, args.draft)
    except LibraryError as e:
        print(f"papers.py {args.command}: {e}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
