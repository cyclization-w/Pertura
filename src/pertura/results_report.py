"""The results report (docs/adr/0020, section 7): figures first, every sentence linked to its record entry, built
from the record only.

report.md, in order (docs/build_goal_session6_fixes.md, item 9):
1. the screen: the first look's design and quality modules in a sentence each, and Figure 1;
2. the results: each result the agent reported (report_result) and the user did not drop, its figure first (drawn by
   code from a run of its findings), the legend, the samples (its n, in the agent's sentence checked against its lead
   tests' units, or counted: samples_line), its
   text, its findings each with its checks (what ran in code, what was answered by hand, or why there are none), the
   prior knowledge beside them and where they differ from it; a record from before the results gives its chosen
   dossiers this way;
3. the open questions, in three to five lines: the open threads, the doubts still open, the rest counted;
4. methods from the record: each module as run, with its choices, the notebook's code runs, and each result's chain
   of steps back to the deposit and the confirmed design (docs/adr/0027), saying which steps are Pertura's and which
   the agent's own code;
5. the appendix: the critic's items in full (docs/adr/0022, section 13: the report is written whatever is open, and
   says what is), the doubts answered, the findings not shown as results, what the record holds and where the user
   ended the analysis;
6. the record entries the report links to, each with its anchor.

The agent's texts carry markers, [run:ID], [finding:ID], [knowledge:N], [result:ID], [thread:ID], [question:ID],
[dossier:ID] or [doubt:ID], which become links to the entries; a marker of an entry the record does not hold is
written as text. check_links: every sentence of the report's body, and every list item, links to an entry the record
holds.
"""
import json
import os
import re
import shutil

from .record import LIMIT, OPEN_ANSWERS, doubt_tested

MARK = re.compile(r"\[(run|finding|knowledge|question|dossier|doubt|result|thread):([^\]\s]+)\]")
LINK = re.compile(r"\]\((?:evidence\.md)?#((run|finding|knowledge|question|dossier|doubt|result|thread)-[^)\s]+)\)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z*(])")
STATUS_WORDS = {"detected": "detected", "not_detected": "not detected", "not_enough_data": "not enough data",
                "cannot_answer": "cannot be answered here", "not_pursued": "not pursued"}
ENTRIES = "## Record entries"
EVIDENCE = "evidence.md"  # beside report.md: the findings' checks and doubts, the appendix and the record's entries


def exists(st, kind, ident):
    if kind == "run":
        return ident in st["runs"]
    if kind == "finding":
        return ident in st["findings"]
    if kind == "question":
        return ident in st["questions"]
    if kind == "dossier":
        return ident in st["dossiers"]
    if kind == "doubt":
        return ident in st["doubts"]
    if kind == "result":
        return ident in (st.get("results") or {})
    if kind == "thread":
        return ident in (st.get("threads") or {})
    return ident.isdigit() and int(ident) in {k["seq"] for k in st["knowledge"]}


def sentences(text):
    return [s.strip() for s in SENTENCE_END.split(" ".join(str(text).split())) if s.strip()]


def unlinked(text, st):
    """The sentences of an agent's text without a marker of an entry the record holds."""
    return [s for s in sentences(text)
            if not any(exists(st, k, i) for k, i in MARK.findall(s))]


def anchor(kind, ident):
    return f"{kind}-{ident}"


def label(kind, ident):
    if kind == "run":
        return f"run {ident.rsplit('-', 1)[-1]}"
    if kind == "knowledge":
        return f"K{ident}"
    if kind in ("dossier", "result"):
        return f"result {ident}"
    return f"{kind} {ident}"


def _listed(words):
    words = [str(w) for w in words]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _quality_line(design, st, cited):
    """The methods' line on the cells left out for their quality: the confirmed design's rule (pertura.quality), cited
    to the first look's run that applied it and counted the cells (A.what_was_done); none without that run."""
    if design is None:
        return []
    runs = [r for r, run in st["runs"].items() if _module(run.get("what", ""))[0] == "A.what_was_done"]
    if not runs:
        return []
    from . import quality
    from .design_gate import node
    rule = quality.describe((node(design, "data.qc") or {}).get("value"),
                            (node(design, "units.unit_column") or {}).get("value"))
    return ["- " + render(f"Quality control, confirmed with the design: {rule} [run:{runs[-1]}].", cited)]


def units_kept(st, jobs_root):
    """The units holding cells after quality control, from the first look's count (A.what_was_done: each unit's
    cells, and those that failed QC); None without that run's table."""
    runs = [r for r, run in st["runs"].items() if _module(run.get("what", ""))[0] == "A.what_was_done"]
    if not runs:
        return None
    import pandas as pd
    try:
        t = pd.read_csv(os.path.join(jobs_root or "", runs[-1], "result.tsv"), sep="	")
    except (OSError, ValueError):
        return None
    if not {"unit", "cells"} <= set(t.columns):
        return None
    left = t["cells"] - (t["failed QC"] if "failed QC" in t.columns else 0)
    return sorted(map(str, t.loc[left > 0, "unit"]))


NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve"}


SAMPLES_FROM = {"lead": "its lead tests took", "kept": "hold cells after quality control",
                "screen": "the screen object holds"}


def samples_line(said, units, observations="cells", source="lead", covariates=()):
    """A result's samples line (docs/adr/0041, step 2): the agent's sentence on the result's n (said: report_result's
    samples), or where it wrote none the counted fact: the units its lead tests took (source lead; else those holding
    cells after quality control, kept, or the screen object's, screen) and what they take as the observations.
    Returns (line, missing): missing says the count a sentence does not give, for the report's open items, or None.
    The sentence is the agent's; code composed it before, and wrote replicates the design did not have
    (pertura_research.retired.samples_sentence_composed_by_code)."""
    units = [str(u) for u in units or []]
    n = len(units)
    whose = SAMPLES_FROM[source]
    if not said:
        if not n:
            return "No units are named by its lead tests.", None
        cov = [str(c) for c in covariates or []]
        return (f"{n} unit{'' if n == 1 else 's'} {whose} ({_ids(units)}), with "
                + ("the units" if observations == "units" else "the cells") + " as the observations"
                + (f" and {_listed(cov)} as {'a covariate' if len(cov) == 1 else 'covariates'}" if cov else "")
                + "."), None
    words = {w.replace(",", "") for w in re.findall(r"[a-z]+|\d[\d,]*", said.lower())}
    if not n or str(n) in words or NUMBER_WORDS.get(n) in words:
        return said, None
    return said, f"its samples sentence does not give the {n} units {whose} ({_ids(units)})"


def tests_sentence(jobs_root, run_ids):
    """How each run a result rests on tested its targets, counted from the run's table: per test, how many targets it
    took (a test by replicate where enough hold both sides, a test on cells where they do not), so a result read as
    an absence says which targets could be tested which way. None where no run's table names its tests."""
    import pandas as pd
    parts = []
    for rid in dict.fromkeys(x for x in run_ids if x):
        path = os.path.join(jobs_root or "", rid, "result.tsv")
        if not os.path.exists(path):
            continue
        try:
            t = pd.read_csv(path, sep="	", usecols=lambda c: c in ("target", "test"))
        except (OSError, ValueError):
            continue
        if not {"target", "test"} <= set(t.columns) or t.empty:
            continue
        per = t.dropna(subset=["test"]).groupby("test")["target"].nunique().sort_values(ascending=False)
        if per.empty:
            continue
        total = int(t["target"].nunique())
        said = ", ".join(f"{n} by {TEST_WORDS.get(test, test)}" for test, n in per.items())
        parts.append(f"of its {total} targets, {said} [run:{rid}]")
    return ("In its runs, " + "; ".join(parts) + ".") if parts else None


CHOICE_WORDS = {"primary": "the primary analysis", "replicate: units": "pseudobulk by sample",
                "replicate: cells": "cells as the observations", "each guide alone": "each guide alone",
                "depth matched": "matched sequencing depth"}


def _choice(name):
    name = str(name)
    if name in CHOICE_WORDS:
        return CHOICE_WORDS[name]
    if name.startswith("half "):
        return name.replace("of the data", "of the cells or samples")
    if name.startswith("reference: "):
        return "against " + name.split(": ", 1)[1].replace("guides", "guide cells")
    return name.replace("_", " ")


OUTCOME_WORDS = {"holds": "it also holds", "contradicted": "it does not hold", "underpowered": "it cannot be told"}


def finding_name(f, short=False):
    """A finding in a few words: its target on its feature (and direction), or, for a finding in a sentence
    (docs/adr/0032, D6), the sentence."""
    if not f.get("target"):
        return f.get("statement") or "a finding"
    feature = str(f["feature"]).removeprefix("table:")  # a score the notebook saved, by its table's name
    return f"{', '.join(f.get('targets') or [f['target']])} on {feature}" + ("" if short else f" ({f['direction']})")


def sensitivity_sentence(fid, f):
    """Which analysis choices a lead finding holds under and which it depends on, and the related measures beside
    it (docs/adr/0022, sections 9 and 15)."""
    from .record import latest_plan
    name = finding_name(f)
    last = latest_plan(f)
    if last is None:
        return f"{name}: not yet rerun under other analysis choices [finding:{fid}]."
    methods = ["primary"] + [v.get("name") for v in last["variants"] if v.get("kind", "method") == "method"]
    res = last["results"]
    holds = [_choice(n) for n in methods if (res.get(n) or {}).get("outcome") == "holds"]
    contra = [_choice(n) for n in methods if (res.get(n) or {}).get("outcome") == "contradicted"]
    weak = [_choice(n) for n in methods if (res.get(n) or {}).get("outcome") == "underpowered"]
    text = f"{name} holds under {_listed(holds)}" if holds else f"{name} holds under none of the analyses tried"
    if contra:
        text += f", and not under {_listed(contra)}"
    if weak:
        text += f"; it is not significant under {_listed(weak)}"
    out = [f"{text} [finding:{fid}]."]
    for r in last.get("related") or []:
        out.append(f"Beside it, {r.get('measures')}, {OUTCOME_WORDS.get(r.get('outcome'), 'it is not measured')} "
                   f"[finding:{fid}].")
    return " ".join(out)


TEST_WORDS = {"edger_ql": "edgeR-QL pseudobulk", "wilcoxon": "Wilcoxon on cells",
              "wilcoxon_same_samples": "Wilcoxon on cells, against the control cells of the same samples",
              "linear_model": "the linear model with the sample", "fisher_same_samples": "Fisher's exact test",
              "propeller": "propeller (speckle) on the samples' proportions",
              "cmh": "the Cochran-Mantel-Haenszel test over the samples",
              "seurat_lr": "Seurat's LR on cells with the sample as a covariate", "mast": "MAST on cells",
              "pydeseq2": "DESeq2 pseudobulk"}
CHECK_ORDER = ("guides_agree", "tests", "other_test", "calibration", "depth_matched", "half", "deposit_calls", "own")
CHECK_NAMES = {"guides_agree": "each guide alone", "tests": "the field's other tests", "other_test": "the other test",
               "calibration":
               "groups of control guides through the same test", "depth_matched": "matched sequencing depth",
               "half": "each half of the data", "deposit_calls": "the deposit's own guide calls",
               "own": "the agent's own check"}


def _g(v):
    return f"{v:.2g}" if isinstance(v, (int, float)) and v == v else "n/a"


def checks_sentence(fid, f):
    """A lead finding's checks in the field's words, each with its n (docs/adr/0025, section 3); a finding the
    robustness engine checked, in a record from before, by its sensitivity sentence."""
    from .record import checks_ran
    checks = f.get("checks") or {}
    if "lead" not in checks and "robustness" in checks:
        return sensitivity_sentence(fid, f)
    name = finding_name(f)
    if not checks_ran(f):
        return f"{name}: its checks have not run [finding:{fid}]."
    if f.get("targets"):  # a claim on several targets: each target's checks, in the order the claim names them
        by = f.get("checks_by_target") or {}
        return " ".join(f"{_target_checks(finding_name({**f, 'targets': None, 'target': t}), by[t])} [finding:{fid}]."
                        if by.get(t) else f"{t}: its checks have not run [finding:{fid}]." for t in f["targets"])
    return f"{_target_checks(name, checks)} [finding:{fid}]."


def _target_checks(name, checks):
    """One target's checks, each with its n."""
    lead = checks.get("lead") or {}
    n = lead.get("numbers") or {}
    if lead.get("outcome") == "not run":
        out = [f"{name}: the lead test gave no result ({_clause(lead.get('reason') or '')})"]
    else:
        out = [f"{name}, by {TEST_WORDS.get(n.get('test'), n.get('test') or 'its test')} ({n.get('cells', 'n/a')} "
               f"cells; effect {_g(n.get('effect'))}): {lead.get('outcome')}"]
    for c in CHECK_ORDER:
        if c not in checks:
            continue
        x, nx = checks[c], checks[c].get("numbers") or {}
        if x["outcome"] == "not run":
            out.append(f"{CHECK_NAMES[c]}: not run ({_clause(x.get('reason') or '')})")
        elif c in ("guides_agree", "half"):
            parts = nx.get("parts") or {}
            said = [(v or {}).get("outcome") for v in parts.values()]
            what = "guides" if c == "guides_agree" else "halves"
            if parts and all(said):  # each part's own outcome, counted: never "agree" over parts that show nothing
                out.append(f"{CHECK_NAMES[c]}: {said.count('agrees')} of {len(parts)} {what} significant its way "
                           f"({x['outcome']})")
            else:  # a record from before the parts kept their outcomes
                out.append(f"{CHECK_NAMES[c]}: {x['outcome']} ({len(parts)} {what})")
        elif c == "tests":
            rows = nx.get("rows") or []
            out.append(f"{CHECK_NAMES[c]}: " + "; ".join(
                f"{r.get('label')} {r.get('outcome')}" + (f" (control-guide groups calling it: "
                                                         f"{r['control_groups_calls']} of {r['control_groups']})"
                                                         if r.get("control_groups") else "") for r in rows))
        elif c == "calibration" and "groups_as_far" in nx:  # a score or a state: the groups' effects against its own
            out.append(f"{CHECK_NAMES[c]}: {nx['groups_as_far']} of {nx.get('groups', 'n/a')} groups go as far its way; "
                       f"{x['outcome']}")
        elif c == "calibration":
            out.append(f"{CHECK_NAMES[c]}: {nx.get('discoveries', 'n/a')} discoveries in {nx.get('groups', 'n/a')} "
                       f"groups; {x['outcome']}")
        elif c == "own":
            out.append(f"{CHECK_NAMES[c]}: {x['outcome']}" + (f" ({_clause(x['reason'])})" if x.get("reason") else "")
                       + (f" [run:{x['run_id']}]" if x.get("run_id") else ""))
        else:
            label = nx.get("label") or CHECK_NAMES[c]
            out.append(f"{label}: {x['outcome']}")
    return "; ".join(out)


OPEN_WORDS = {
    "left at the end": "Left when the analysis ended",
    "figure drawn after the set's review": "A figure drawn after the review of the figures as a set",
    "question without a status": "Not yet answered",
    "finding without its evidence": "A finding without part of its evidence",
    "finding whose checks have not run": "A finding whose checks have not run",
    "finding whose checks disagree": "A finding whose checks disagree",
    "result without prior knowledge looked up": "A result whose prior knowledge was not looked up",
    "difference from prior knowledge not looked up": "A difference from prior knowledge not looked up",
    "lead finding whose checks have not run": "A lead finding whose checks have not run",
    "lead finding whose checks disagree": "A lead finding whose checks disagree",
    "doubt without an answer": "A doubt raised and not answered",
    "doubt still open": "An alternative explanation tested and still open",
    "question closed but judged not sufficient": "An answer judged not yet supported by its evidence",
    "results proposed, none chosen": "Results proposed, none chosen",
    "result part missing": "A result without some of its parts",
    "result's main figure covers few targets or genes": "A result whose main figure covers few targets or genes",
    "lead finding without its sensitivity to analysis choices": "A lead finding not yet rerun under other choices",
    "lead finding that holds under no analysis choice": "A lead finding that holds under no analysis choice",
    "result figure not in the record": "A result's figure not in the record",
    "figure review due": "A main figure not yet reviewed",
    "skeptic due": "A finding not yet checked for alternative explanations",
    "advisor due": "A result not yet placed against the literature",
    "run without output": "An analysis without output",
    "finding on the previous screen": "A finding resting on an earlier build of the screen object",
    "run without a reading": "An analysis not yet read",
    "figure with an open issue": "A figure with an open issue",
    "figures as a set with open issues": "The figures as a set, with the open issues of their last review",
    "figure set review due": "The figures not yet reviewed as a set",
    "thread without an end": "A line of analysis taken up and not yet ended",
    "place a line stopped without an answer": "A place the analysis stopped, with no route around it given",
    "the user's question not recorded": "The user's question, which leads the analysis, not yet recorded",
    "no paradigm chosen": "No approach (a paradigm card) chosen yet"}
CHECK_WORDS = {"guides_agree": "guide concordance", "robustness": "sensitivity to analysis choices"}


def _open_marker(st, item):
    """The record entry an open item is about, as a marker."""
    kind, ident = item["kind"], item["id"]
    if kind in ("question without a status", "question closed but judged not sufficient"):
        return f"[question:{ident}]"
    if kind in ("finding without its evidence", "finding whose checks have not run", "finding whose checks disagree",
                "skeptic due", "finding on the previous screen", "difference from prior knowledge not looked up"):
        return f"[finding:{ident}]"
    if kind in DOUBT_ITEMS:
        return _doubt_markers(st, ident)
    if kind in ("thread without an answer", "thread without an end"):
        return f"[thread:{ident}]"
    if kind == "place a line stopped without an answer":
        return f"[thread:{ident}]" if ident in (st.get("threads") or {}) else _doubt_markers(st, ident)
    if kind.startswith("run "):
        return f"[run:{ident}]"
    if kind in ("figure review due", "figure with an open issue", "figure drawn after the set's review"):
        return f"[run:{st['figures'][ident]['run_id']}]"
    if kind in ("figures as a set with open issues", "figure set review due"):
        return " ".join(f"[result:{r['result_id']}]" for r in results_of(st))
    if kind == "results proposed, none chosen":
        return " ".join(f"[dossier:{d}]" for d in st["dossiers"])
    if ((kind.startswith("lead finding") or kind in ("result without prior knowledge looked up", "advisor due"))
            and item["id"] in (st.get("results") or {})):
        return f"[result:{item['id']}]"
    if kind in ("the user's question not recorded", "no paradigm chosen"):  # the plan's, not one entry's
        return f"[question:{st['order'][0]}]" if st["order"] else ""
    if kind == LEFT_AT_THE_END:  # a result's next question, or a set or kind of the survey's, which has no entry
        return f"[result:{ident}]" if ident in (st.get("results") or {}) else ""
    return f"[dossier:{ident}]"


# what the research lead's complete waits on, listed once an end is recorded (docs/adr/0049, D4)
LEFT_AT_THE_END = "left at the end"


DOUBT_ITEMS = ("doubt without an answer", "doubt still open")
LIMIT_WORDS = {"design": "the design cannot answer it", "budget": "left for the budget",  # budget: answers before 11-15
               "tool": "no tool here does it"}


def open_lines(st, items):
    """The report's list of what is still open, one linked line per item; a doubt in full, with what would change the
    judgement, as the report prints it only here."""
    out = []
    for i in items:
        what = str(i["what"])
        if i["kind"] == "finding without its evidence":
            what = ", ".join(CHECK_WORDS.get(c.strip(), c.strip()) for c in what.split(","))
        words = OPEN_WORDS.get(i["kind"], i["kind"][0].upper() + i["kind"][1:])
        if i["kind"].endswith(" due"):  # its what tells the agent which reviewer to launch: the words say it here
            out.append(f"{words} {_open_marker(st, i)}.")
            continue
        would = (st["doubts"].get(i["id"]) or {}).get("would_change") if i["kind"] in DOUBT_ITEMS else None
        marker = _open_marker(st, i)
        out.append(f"{words}: {_clause(what)}" + (f"; what would change the judgement: {_clause(would)}" if would
                                                   else "") + (f" {marker}" if marker else "") + ".")
    return out


def doubts_in_the_appendix(st, items):
    """The doubts the appendix prints in full: the open ones among the items, and those answered."""
    return ({i["id"] for i in items if i["kind"] in DOUBT_ITEMS}
            | {did for did, d in st["doubts"].items() if d.get("answer") and d["answer"]["outcome"] not in OPEN_ANSWERS})


class Cited(set):
    """The entries a report links to, with the record's state: render writes an ID the state does not hold as text."""

    def __init__(self, st):
        super().__init__()
        self.st = st


def render(text, cited):
    """Markers as links; each entry linked is added to `cited`. Given a Cited, a marker of an entry the record does not
    hold is written as text, not as a link to no entry."""
    def link(m):
        kind, ident = m.group(1), m.group(2)
        if isinstance(cited, Cited) and not exists(cited.st, kind, ident):
            return label(kind, ident)
        cited.add((kind, ident))
        return f"[{label(kind, ident)}](#{anchor(kind, ident)})"
    return MARK.sub(link, " ".join(str(text).split()))


def _doubt_markers(st, ident):
    """A doubt's marker, with its finding's or question's."""
    d = st["doubts"][ident]
    on = ([f"[finding:{d['finding_id']}]"] if d.get("finding_id") else
          [f"[question:{d['question_id']}]"] if d.get("question_id") else [])
    return " ".join([f"[doubt:{ident}]"] + on)


def doubt_lines(st):
    """Each answered doubt, with the finding (or question) it is on and its answer, one linked line each. A doubt not
    answered, or answered still open, is one of the open items."""
    out = []
    for did, d in st["doubts"].items():
        a = d.get("answer")
        if a and a["outcome"] not in OPEN_ANSWERS:
            runs = " ".join(f"[run:{r}]" for r in a["run_ids"])
            said = a["outcome"] + (f" ({LIMIT_WORDS[a['limit']]})" if a.get("limit") in LIMIT_WORDS else "")
            untested = a["outcome"] not in (LIMIT,) and not doubt_tested(st, did)
            out.append(f"{_clause(d['text'])} {_doubt_markers(st, did)}. Answered: {said}, "
                       f"{_clause(a['reason'])}" + (f" {runs}" if runs else "")
                       + (f", with no run made after the doubt; its test: {_clause(d['would_change'])}"
                          if untested and d.get("would_change") else
                          ", with no run made after the doubt" if untested else "") + ".")
    return out


def _summary(jobs_root, run):
    path = os.path.join(jobs_root, run["job_id"] or run["run_id"], "result.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            s = json.load(fh).get("summary")
        if s:
            return s
    return sentences(run.get("reading") or run["what"])[0]


def _status_words(q):
    """A closed question's status in words, saying who left a question not pursued (docs/adr/0030)."""
    words = STATUS_WORDS[q["status"]]
    if q["status"] == "not_pursued":
        words += " (the user's decision)" if q.get("closed_by") == "user" else " (left by the analysis agent)"
    return words


def _clause(text):
    return str(text).strip().rstrip(".")


def _dependence(verdict, reason):
    """What a verdict rests on, in brief, for the report (the full reason stays in the record): where a robust
    finding was seen on its own, or what a conditional one depends on."""
    reason = str(reason or "")
    if verdict == "robust":
        m = re.search(r"seen alone in ([^;(]+)", reason)
        return f"seen on its own in {m.group(1).strip()}" if m else ""
    for marker in ("contradicted by", "seen only under"):
        if marker in reason:
            return marker + reason.split(marker, 1)[1].split(" (")[0].rstrip()
    return reason.split(";")[0]


def _units_of(st, finding_ids):
    """The units the findings' lead tests took (their numbers' replicates, else their units), or None: a result's n
    counts these, not every unit of the screen (GSE267740: 12 captures, of which 5 hold guide-bearing cells)."""
    out = set()
    for fid in finding_ids or []:
        nums = (((st["findings"].get(fid) or {}).get("checks") or {}).get("lead") or {}).get("numbers") or {}
        out |= {str(u) for u in nums.get("replicates") or nums.get("units") or []}
    return sorted(out) or None


def _units_and_source(st, finding_ids, kept, screen):
    """The units a result's n counts, and where they come from: its lead tests' (lead), else the units holding cells
    after quality control (kept), else the screen object's (screen)."""
    lead = _units_of(st, finding_ids)
    if lead:
        return lead, "lead"
    if kept:
        return kept, "kept"
    return sorted(str(u) for u in ((screen or {}).get("units") or {})), "screen"


def _observations(st, lead_findings):
    """What the lead findings' lead test takes as its observations: 'units' for pseudobulk, else 'cells'."""
    for fid in lead_findings:
        lead = (st["findings"][fid].get("checks") or {}).get("lead") or {}
        test = (lead.get("numbers") or {}).get("test")
        if test:
            return "units" if test == "edger_ql" else "cells"
        from .record import latest_plan
        plan = latest_plan(st["findings"][fid])
        if plan:
            return (plan["primary"] or {}).get("unit", "cells")
    return "cells"


def _module(what):
    """(module, how it ran) of a module run's description, or (None, None): its choices with their values, and the
    names of the inputs it was given."""
    m = re.match(r"module (\S+)(?: with (.*))?$", what)
    if not m:
        return None, None
    try:
        given = json.loads(m.group(2)) if m.group(2) else {}
    except ValueError:
        given = {}
    how = [f"{k} {v}" for k, v in (given.get("choices") or {}).items()]
    if given.get("inputs"):
        how.append("the input" + ("s " if len(given["inputs"]) > 1 else " ") + ", ".join(given["inputs"]))
    if given.get("cells"):  # the cells it ran on: a kept cell set, not the whole screen
        how.append(f"on the cell set {given['cells']}")
    return m.group(1), ", ".join(how)


def _n(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def _finding_line(f):
    """A finding in a few words: its target, what it moves and which way, and the cells it rests on."""
    way = {"up": "raises", "down": "lowers"}.get(f.get("direction"), "changes")
    where = f" (cells: {f['cells']})" if f.get("cells") else ""
    if not f.get("target"):  # a finding in a sentence (docs/adr/0032, D6)
        return f"{f.get('statement') or 'a finding'}{where}"
    targets = f.get("targets") or [f["target"]]  # a claim on several targets names each
    way = way[:-1] if len(targets) > 1 else way
    return f"{', '.join(targets)} {way} {str(f['feature']).removeprefix('table:')}{where}"


def own_measure(st, f):
    """Whether a finding rests on the agent's own code only: it names no block call, and every step of its runs'
    chain other than the base is a notebook cell (no module, block or other Pertura job)."""
    from . import flow
    runs = list(f.get("run_ids") or [])
    if not runs or f.get("block_call") or any(r not in st["runs"] for r in runs):
        return False
    return all(s["by"] == "agent" or s["step"] in flow.BASE for s in flow.chain(st, runs))


def _ids(ids, n=4):
    ids = [str(i) for i in ids]
    return _listed(ids) if len(ids) <= n else ", ".join(ids[:n]) + f" and {len(ids) - n} more"


def chain_lines(st, did, number, mark=None):
    """The steps that made a result, from the deposit and the confirmed design (pertura.flow.chain): one line per
    step, saying which are Pertura's (the base, a module, a block, a check) and which the agent's own code, with what
    each used and made. mark: the result's marker ([result:ID], or [dossier:ID] in a record from before)."""
    from . import flow
    mark = mark or f"[dossier:{did}]"
    steps = flow.chain(st, flow.starts(st, [did]))
    if not steps:
        return []
    own = sum(1 for s in steps if s["by"] == "agent")
    out = ["- " + f"Result {number} was made in {_n(len(steps), 'step')}, {own} of them the agent's own code in the "
           f"notebook, the rest Pertura's {mark}."]
    for k, s in enumerate(steps, 1):
        used = f"; it used {_ids(s['uses'])}" if s["uses"] else ""
        made = f" and made {_ids(s['makes'])}" if s["makes"] and s["step"] not in flow.BASE else ""
        if s["step"] in flow.BASE:
            out.append(f"  - Step {k}, Pertura: {s['what']} {mark}.")
            continue
        module, how = _module(s["what"]) if s["by"] == "pertura" else (None, None)
        what = f"{module}" + (f" with {how}" if how else "") if module else _clause(s["what"])
        who = "the agent's own code" if s["by"] == "agent" else "Pertura"
        link = f"[run:{s['run_id']}]" if s.get("run_id") in st["runs"] else mark
        out.append(f"  - Step {k}, {who}: {_clause(what)}{used}{made} {link}.")
    return out


def _legend(name, title, did, tiles):
    """A figure's legend: its name and the result's title, then what each tile shows, linked to its run."""
    return " ".join([f"**{name}.** {_clause(title)} [dossier:{did}]."] +
                    [f"**{t['letter']}**, {_clause(t['caption'])} [run:{t['run_id']}]." for t in tiles])


def _main_diagnostics(jobs_root, st, run_id):
    run = st["runs"].get(run_id) or {}
    path = os.path.join(jobs_root, run.get("job_id") or run_id or "", "result.json")
    if run_id and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("diagnostics") or {}
    return {}


def _tested_words(jobs_root, st, rids):
    """The runs' multiple testing for the methods: their tests, what was corrected together, how the targets were
    chosen; "" where no run says."""
    from .question_modules.contract import multiple_testing_words
    said = list(dict.fromkeys(w for w in (multiple_testing_words(_main_diagnostics(jobs_root, st, r).get(
        "multiple_testing")) for r in rids) if w))
    return f" ({'; '.join(said)})" if said else ""


def _in_story_order(results):
    """The results ordered by the questions that raised them: each after the result it names as raised_by, the rest
    in the order reported."""
    ids = {r["result_id"] for r in results}
    after = {}
    for r in results:
        if r.get("raised_by") in ids and r["raised_by"] != r["result_id"]:
            after.setdefault(r["raised_by"], []).append(r)
    out, seen = [], set()

    def walk(r):
        if r["result_id"] in seen:
            return
        seen.add(r["result_id"])
        out.append(r)
        for c in after.get(r["result_id"], []):
            walk(c)
    for r in results:
        if r.get("raised_by") not in ids:
            walk(r)
    for r in results:  # a loop of raised_by: in the order reported
        walk(r)
    return out


def _story_lines(st, cited):
    """The report's opening: the story the agent wrote, or, where it wrote none, the research lead's last (docs/adr/
    0036, D6), as code keeps the report written (P5)."""
    story = (st.get("story") or {}).get("text") or ((st.get("reviews") or [{}])[-1].get("story") or None)
    return ["## The story", "", render(story, cited), ""] if story else []


def _minute(t):
    return str(t or "")[:16].replace("T", " ")


def _marker_before(st, seq=None):
    """The entry an end's sentence links to: the last finding before it, else the latest result or run."""
    found = [fid for fid, f in st["findings"].items() if seq is None or (f.get("seq") or 0) < seq]
    if found:
        return f" [finding:{found[-1]}]"
    if st.get("results"):
        return f" [result:{list(st['results'])[-1]}]"
    return f" [run:{list(st['runs'])[-1]}]" if st["runs"] else ""


OPEN_AT_THE_END = {"checks_running": ("finding", "still being checked"), "doubts_open": ("doubt", "without an answer"),
                   "choices_open": ("choice", "not yet tried")}


def _still_open(open_now):
    """What stayed open, from stop-facts' lists by name (open_now), as the end sentence's last clause, so the sentence
    still links its entry; nothing where it is not known."""
    if open_now is None:
        return ""
    said = [f"{_n(len(ids), word)} {tail} ({', '.join(map(str, ids))})"
            for key, (word, tail) in OPEN_AT_THE_END.items() for ids in [open_now.get(key) or []] if ids]
    return ("; still open: " + ", ".join(said)) if said else "; nothing in the record is still open"


def end_line(st, open_now=None):
    """Which end the record holds, for the report's top (docs/adr/0049, D7): the user's enough in their words (the
    analysis's own, unattended), or none, and then the session ended with its answer written, at a cap or by a stop,
    or is still running: the record cannot tell these apart. What stayed open follows (open_now: stop-facts' lists by
    name). An end that later work follows says so. Each sentence links its entry. (The research lead's complete, the
    other end the record held, left with the lead on 2026-11-20.)"""
    ended = st.get("enough")
    if ended:
        who = "The analysis ended (unattended)" if ended.get("by_analysis") else "The user ended the analysis"
        before = [f for f in st["findings"].values() if (f.get("seq") or 0) < ended["seq"]]
        line = (f"{who} at {_minute(ended['time'])}, after {_n(len(before), 'finding')}"
                f"{_marker_before(st, ended['seq'])}: \"{_clause(ended['words'])}\"{_still_open(open_now)}.")
        threads = [(t, th) for t, th in (st.get("threads") or {}).items() if (th.get("seq") or 0) > ended["seq"]]
        results = [(r, x) for r, x in (st.get("results") or {}).items() if (x.get("seq") or 0) > ended["seq"]]
        if threads or results:
            last = max([th.get("time") or "" for _, th in threads] + [x.get("time") or "" for _, x in results])
            mark = f"[result:{results[-1][0]}]" if results else f"[thread:{threads[-1][0]}]"
            line += (f" The analysis went on after it: {_n(len(threads), 'thread')} opened and "
                     f"{_n(len(results), 'result')} reported or changed, the last at {_minute(last)} {mark}.")
        return line
    return ("No enough is recorded: the session ended with its answer written, at a cap or by a stop, or it is still "
            f"running, and it can resume{_still_open(open_now)}{_marker_before(st)}.")


def _chain_breaks(results):
    """Each break in the chain of results, for the appendix's open items (a lint line under the story read as part of
    it, GSE267740 on a15df12): a result after the first that does not say which result or finding raised its
    question."""
    return [f"Result {r['result_id']} does not say which earlier result raised its question {r['marker']}."
            for r in results[1:] if not r.get("raised_by")]


def results_of(st):
    """The results the report shows, in order: those reported and not dropped (docs/build_goal_session6_fixes.md,
    item 9); in a record from before them, its chosen dossiers, each read as a result: its title, its lead findings,
    its main run, and its panel's figure and text."""
    from .record import results_shown
    shown = [{**r, "marker": f"[result:{r['result_id']}]"} for r in results_shown(st)]
    if shown:
        return _in_story_order(shown)
    out = []
    for did in st.get("chosen") or []:
        d = st["dossiers"][did]
        panel = d["parts"].get("panel_and_paragraph") or {}
        out.append({"result_id": did, "title": d["title"], "finding_ids": list(d["lead_findings"]), "figure": None,
                    "figure_path": panel.get("figure"), "figure_run": d["main_run"], "text": panel.get("text"),
                    "version": 1, "decision": "confirmed", "marker": f"[dossier:{did}]", "parts": d["parts"]})
    return out


def _result_figure(st, jobs_root, r):
    """The PNG of a result's figure: the saved figure it names, else its run's own figure."""
    if r.get("figure_path"):
        return r["figure_path"]
    fig = st["figures"].get(r.get("figure") or "")
    if fig:
        paths = fig.get("paths") or {}
        return paths.get("png") if isinstance(paths, dict) else None
    run = st["runs"].get(r.get("figure_run") or "") or {}
    for path in (run.get("figure"), os.path.join(jobs_root, str(run.get("job_id") or r.get("figure_run") or ""),
                                                 "figure.png")):
        if path and os.path.exists(path):
            return path
    return None


def _path_sentence(st, way):
    """A result's path in the tree, in sentences: the steps from the first look to it, each a thread, and each fork
    beside the path with its one change and what it found."""
    ths = st.get("threads") or {}
    steps = " Then ".join(f"{_clause(str(ths[t].get('about') or ''))[:160]} [thread:{t}]." for t in way["path"])
    out = [f"From the first look: {steps}"] if steps else []  # each sentence links its entry (check_links)
    for f in way["forks"]:
        th = ths[f]
        found = " ".join(f"[finding:{x}]" for x in th.get("findings") or [])
        out.append(f"Beside it, a fork with {_clause(str(th.get('choice') or 'one choice changed'))} "
                   f"({th.get('status')}): {found or 'no finding yet'} [thread:{f}].")
    return " ".join(out)


def _next_question(st, tid):
    """A result's next question (a thread next_of it, docs/adr/0048) and its end, in sentences: the findings that
    answer it, the reason it ended, or that it is open."""
    th = st["threads"][tid]
    asked = _clause(str(th.get("about") or "").strip())
    found = " ".join(f"[finding:{f}]" for f in th.get("findings") or [])
    ends = {"explained": "Answered", "contradicted": "Answered against it", "out of data": "The data cannot answer it",
            "set aside": "Set aside"}
    asked += "" if asked.endswith(("?", "!")) else "."
    if th.get("status") in ends:
        from .record import probed_by_nothing
        said = found or _clause(str(th.get("reason") or "").strip())
        said += "; no run was made for it" if probed_by_nothing(st, tid, th) and said else ""  # docs/adr/0049
        return f"{asked} {ends[th['status']]}: {said}." if said else f"{asked} {ends[th['status']]}."
    return f"{asked} Open."


def _checks_words(st, fid):
    """A finding's checks column: what ran in code, what was answered by hand, or why there are none."""
    from .record import checkable, checks_ran
    f = st["findings"][fid]
    if checkable(f) and (checks_ran(f) or "robustness" in (f.get("checks") or {})):
        said = checks_sentence(fid, f)
    elif checkable(f) and f.get("kind") in ("custom", "interaction"):
        said = (f"{finding_name(f)}: no check runs on it in code ("
                + ("the agent's own code" if f["kind"] == "custom" else "a combination, checked by E.combinations' own "
                   "agreement between units") + f") [finding:{fid}].")
    elif checkable(f):
        said = f"{finding_name(f)}: its checks have not run [finding:{fid}]."
    elif f.get("kind") == "description":  # said so when it was recorded (docs/adr/0038, D2)
        said = f"A description (a pattern, a count or a technical fact): no check runs on it in code [finding:{fid}]."
    else:
        said = (f"No check runs on it in code: a finding in a sentence, whose checks need a target, a feature and a "
                f"direction [finding:{fid}].")
    by_hand = [d for d in st["doubts"].values() if d.get("finding_id") == fid and d.get("answer")]
    if by_hand:
        said += " Answered by hand: " + "; ".join(
            f"{_clause(d['text'])}, {d['answer']['outcome']}" for d in by_hand) + f" [finding:{fid}]."
    return said


def _first_look_lines(st, jobs_root):
    """The screen, from the first look: each design and quality module's latest run (A, B), in one sentence; in a
    record from before, each closed design and quality question."""
    out = []
    closed = [qid for qid in st["order"] if st["questions"][qid]["letter"] in "AB" and st["questions"][qid]["status"]]
    if closed:
        for qid in closed:
            q = st["questions"][qid]
            if q["evidence"]:
                rid = q["evidence"][0]
                out.append(f"{q['item'][0].upper()}{q['item'][1:]} ({STATUS_WORDS[q['status']]}): "
                           f"{_clause(_summary(jobs_root, st['runs'][rid]))} [question:{qid}] [run:{rid}].")
            else:
                out.append(f"{q['item'][0].upper()}{q['item'][1:]}: {_status_words(q)}, {_clause(q['reason'])} "
                           f"[question:{qid}].")
        return out
    latest = {}
    for rid, r in st["runs"].items():
        q = str(r.get("question_id") or "")
        if r.get("job_id") and q[:1] in ("A", "B") and r["state"] not in ("running", "failed", "stopped"):
            latest[q] = rid
    for q, rid in sorted(latest.items()):
        out.append(f"{_clause(_summary(jobs_root, st['runs'][rid]))} ({q}) [run:{rid}].")
    return out


def _with_the_proposal(design):
    """The user's note when they confirmed the design or the cells' rule, word for word (what they did not know is
    said there, docs/adr/0053); and, in a design confirmed item by item, the facts answered "I don't know", kept as
    Pertura proposed them (docs/adr/0047, A1)."""
    from .design_service import TITLES, form_value
    out, notes = [], {}
    for c in (design or {}).get("confirmations") or []:
        if not isinstance(c, dict):
            continue
        note = str(c.get("note") or "").strip()
        if c.get("answer") == "unsure":
            out.append(f"{TITLES.get(c['key'], c['key'])}: {form_value(design, c['key'])}, as Pertura proposed it, "
                       "since the user did not know" + (f' (the form\'s note: "{note}")' if note else "") + ".")
        elif c.get("answer") == "confirm" and note:
            notes.setdefault(note, set()).add("the cells' quality rule" if c.get("key") == "data.qc" else "the design")
    for note, what in notes.items():
        out.append(f"The user's note when they confirmed {' and '.join(sorted(what))}, word for word: \"{note}\".")
    return out


def _decided_for_the_user(st, design=None):
    """What the analysis decided where the user would have: the design's facts the user did not know, kept as
    proposed; and, when they ran Pertura unattended, each result kept or dropped with its reason, each module menu run,
    and the end with its reason."""
    # each with its time, so a reason that a later review overtook reads as history (docs/adr/0038, D6)
    out = [f"Result {rid} ({_clause(r['title'])}) {r['decision']}"
           f"{' at ' + str(r['decided_time'])[:16].replace('T', ' ') if r.get('decided_time') else ''}: "
           f"{_clause(r['reason'])} [result:{rid}]."
           for rid, r in (st.get("results") or {}).items() if r.get("by_analysis")]
    out += [f"The module menu at {str(m.get('time') or '')[:16].replace('T', ' ')}: ran {_listed(m['chosen'])}, as "
            "the module picker picked them" for m in st.get("module_menus") or []
            if m["kind"] == "module_menu_answered" and m.get("action") == "unattended" and m.get("chosen")]
    ended = st.get("enough")
    if ended and ended.get("by_analysis"):
        out.append(f"The analysis ended at {ended['time']}: {_clause(ended['words'])}.")
    return _with_the_proposal(design) + out


OVERVIEW = "A.screen_overview"


def screen_figure(st, jobs_root, out_dir):
    """Figure 1, the screen: the figure of this build's latest A.screen_overview run, copied to
    figures/screen.png, and its legend as the run wrote it, each panel linked to the run it reads; None where the
    record has no such run."""
    from .record import module_runs
    done = [r for r in module_runs(st).get(OVERVIEW, []) if r["state"] == "done"]
    if not done:
        return None
    rid = done[-1]["run_id"]
    folder = os.path.join(jobs_root, rid)
    try:
        with open(os.path.join(folder, "result.json"), encoding="utf-8") as fh:
            diagnostics = json.load(fh).get("diagnostics") or {}
    except (OSError, ValueError):
        return None
    panels, pooled = diagnostics.get("legend") or [], diagnostics.get("conditions_pooled") or []
    png = os.path.join(folder, "figure.png")
    if not os.path.exists(png):
        return None
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
    shutil.copyfile(png, os.path.join(out_dir, "figures", "screen.png"))
    return " ".join([f"**Figure 1.** The screen at a glance [run:{rid}]."] +
                    ([f"Its measures pool the cells of the screen's {len(pooled)} conditions ({', '.join(pooled)})."]
                     if pooled else []) +
                    (["Each sample holds several of them, so each target is read against the control cells of its "
                      "own samples and conditions, in its mix."] if diagnostics.get("matched_within_conditions")
                     else []) +
                    [f"**{x['letter']}**, {_clause(x['text'])} "
                     f"[run:{x['run'] if x.get('run') in st['runs'] else rid}]." for x in panels])


SHIFTS_SHOWN = (2, 2, 4)  # Figure 1 in words: a set's states and targets per state; the other targets' shifts
READINGS_QUOTED = 200  # the characters of a finding's first sentence quoted beside a set or a shift


def screen_reading(st, jobs_root):
    """Figure 1 in words, written by code from the runs it draws, so that every report states the screen's structure
    whether or not a result took it up: the targets past their line (A.screen_overview); each set of targets alike,
    with the targets opposite it (E.similar_targets, as its lines give them), the states its targets and those opposite
    move (C.states_and_composition, q below candidates.Q) and the finding that reads it; and the largest state shifts
    of the targets no set names, each with the finding that reads it. [] where neither the overview nor the similarity
    ran on this build."""
    import pandas as pd
    from . import candidates
    from .question_modules.e import _set_line
    from .record import module_runs
    by = module_runs(st)

    def latest(module):
        done = [r["run_id"] for r in by.get(module, []) if r["state"] == "done"]
        return done[-1] if done else None

    def table(rid, name):
        try:
            return pd.read_csv(os.path.join(jobs_root, rid, f"{name}.tsv"), sep="\t") if rid else None
        except (OSError, ValueError):
            return None

    def diagnostics(rid):
        try:
            with open(os.path.join(jobs_root, rid, "result.json"), encoding="utf-8") as fh:
                return json.load(fh).get("diagnostics") or {}
        except (OSError, ValueError, TypeError):
            return {}
    out, sets = [], []
    ov = latest(OVERVIEW)
    # the runs Figure 1's panels draw, so its words name the states and sets its panels show (a later rerun of the
    # states numbers them anew)
    drawn = {x["input"]: x["run"] for x in diagnostics(ov).get("legend") or [] if x.get("input") and x.get("run")}
    targets = table(ov, "targets")
    if targets is not None and "target" in targets:
        far = candidates.far_from_controls(targets)
        named = ", ".join(t["target"] for t in far["targets"])
        out.append(f"{far['count']} of {len(targets)} targets lie past their line"
                   + (f", the farthest first: {named}" if named else "") + f" [run:{ov}].")
    sim = drawn.get("similar") or latest("E.similar_targets")
    pairs = table(sim, "result")
    if pairs is not None:
        lines = diagnostics(sim)
        if lines.get("control_pairs_95th_percentile") is not None:  # the sets as the module drew them (e.py)
            sets = candidates.apart(candidates.sets_alike(pairs, reference=lines["control_pairs_95th_percentile"],
                                                          low=lines.get("control_pairs_5th_percentile"))["sets"])
            out.append(f"{len(sets)} sets of targets alike, each read against the control-guide pairs [run:{sim}]:"
                       if sets else f"No set of targets alike past the control-guide pairs [run:{sim}].")
    if not out:
        return []
    states = drawn.get("states") or latest("C.states_and_composition")
    per = table(states, "result")
    hits = None
    if per is not None and {"target", "state", "q", "share", "control_share"} <= set(per):
        hits = per[pd.to_numeric(per["q"], errors="coerce") < candidates.Q].assign(
            share=lambda d: pd.to_numeric(d["share"], errors="coerce"),
            control_share=lambda d: pd.to_numeric(d["control_share"], errors="coerce"),
            size=lambda d: (d["share"] - d["control_share"]).abs()).sort_values("size", ascending=False)

    def moved(names):  # the states these targets move, the most targets first, each with its largest shifts
        rows = hits[hits["target"].isin(names)] if hits is not None else pd.DataFrame()
        top = (sorted(rows.groupby("state"), key=lambda x: (-len(x[1]), -x[1]["size"].max()))[:SHIFTS_SHOWN[0]]
               if len(rows) else [])
        return ", ".join(f"{state} (" + ", ".join(f"{r.target} {r.share:.0%} against {r.control_share:.0%}"
                                                  for r in h.head(SHIFTS_SHOWN[1]).itertuples()) + ")"
                         for state, h in top)

    def named(f):
        return set(f.get("targets") or ([f["target"]] if f.get("target") else []))

    def other_states(f):  # a finding on another states run numbers its states anew, so it would not match the figure
        return any(rid != states and str((st["runs"].get(rid) or {}).get("what") or "").startswith(
            "module C.states_and_composition") for rid in [f.get("state_run")] + list(f.get("run_ids") or []) if rid)

    def read_in(names):  # the findings naming the most of these targets first, then the latest; one quoted
        reading = sorted(((len(named(f) & names), i, fid, f) for i, (fid, f) in enumerate(st["findings"].items())
                          if named(f) & names and not other_states(f)), key=lambda x: (-x[0], -x[1]))
        if not reading:
            return "no finding reads it"
        _, _, fid, f = reading[0]
        text = _clause((sentences(f.get("statement") or _finding_line(f)) or [""])[0])
        if len(text) > READINGS_QUOTED:  # cut at a word; the link leads to the whole
            text = text[:READINGS_QUOTED].rsplit(" ", 1)[0] + " ..."
        more = len(reading) - 1
        return (f"read in: {text} [finding:{fid}]"
                + (f", and {more} more finding{'s' if more > 1 else ''}" if more > 0 else ""))
    for s in sets:
        members = set(s["targets"])
        opposite = {o["target"] for o in s.get("opposite") or []}
        words = [_set_line(s) + "."]
        own, other = moved(members), moved(opposite)
        if own:
            words.append(f"Its targets move {own}.")
        elif hits is not None:
            words.append(f"No state share of its targets moves at q < {candidates.Q}.")
        if other:
            words.append(f"Those opposite it move {other}.")
        reading = read_in(members)
        words.append(reading[0].upper() + reading[1:] + ".")
        if opposite and not any(named(f) & opposite for f in st["findings"].values()):
            words.append("No finding reads those opposite it.")
        out.append("- " + " ".join(words))
    if hits is not None:  # panel e: the largest shifts of the targets no set line names
        told = set().union(*([set(s["targets"]) | {o["target"] for o in s.get("opposite") or []} for s in sets]
                             or [set()]))
        rest = hits[~hits["target"].isin(told)].head(SHIFTS_SHOWN[2])
        if len(rest):  # each target once, with its shifts among these, in the order of its largest
            out.append(f"- The largest state shifts of the other targets [run:{states}]: " + "; ".join(
                f"{t} in " + ", ".join(f"{r.state}, {r.share:.0%} against {r.control_share:.0%}"
                                       for r in rest[rest["target"] == t].itertuples()) + f" ({read_in({t})})"
                for t in dict.fromkeys(rest["target"])) + ".")
    out[0] = "**The screen's structure.** " + out[0]
    return out


def design_figure(design, screen_dir, out_dir):
    """Figure 1 where no A.screen_overview ran on this build (session 13): the screen's design alone, drawn from the
    confirmed design and the screen object's files, saved as figures/screen.png; its legend, or None where it cannot be
    drawn."""
    from .question_modules import charts, overview
    try:
        stages = overview.design_stages_from_files(screen_dir, design)
        os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
        charts.design_panel(stages, path=os.path.join(out_dir, "figures", "screen.png"))
    except Exception:  # Figure 1 never stops the report; without it the first look's summaries stand
        return None
    return ("**Figure 1.** The screen's design, from the confirmed design and the screen object: "
            + "; ".join(f"{heading.lower()}, {', '.join(lines)}" for heading, lines in stages) + ".")


def _open_questions(st, items):
    """Still open, in three to five lines: the open threads, strongest first, the doubts still open, and the
    rest of the critic's items, listed in the appendix."""
    from . import threads
    from .record import set_aside_unprobed
    ths = st.get("threads") or {}
    unanswered = [i for i in items if i["kind"] == "thread without an answer"]
    left = {i["id"] for i in unanswered}
    open_ = [t for t in threads.strength_order(st) if ths[t].get("status") in ("open", "followed") and t not in left]
    tops = [f"{_clause(ths[t].get('about'))} ({ths[t]['status']}) [thread:{t}]." for t in open_[:3]]
    out = []
    at_end = [i for i in items if i["kind"] == LEFT_AT_THE_END]  # docs/adr/0049, D4: what the end left, named
    if at_end:
        more = f"; and {len(at_end) - 4} more, in the appendix" if len(at_end) > 4 else ""
        out.append("Left when the analysis ended: " + "; ".join(
            _clause(i["what"]) + (f" {_open_marker(st, i)}" if _open_marker(st, i) else "") for i in at_end[:4])
                   + more + ".")
    unprobed = [t for t, th in ths.items() if th.get("status") == "set aside" and set_aside_unprobed(st, t, th)]
    if unprobed:
        out.append(f"{_n(len(unprobed), 'thread')} off the main line set aside before anything probed it: " + "; ".join(
            f"{_clause(ths[t].get('about'))} ({_clause(ths[t].get('reason') or 'no reason given')}) [thread:{t}]"
            for t in unprobed[:3]) + ".")
    doubts = [i for i in items if i["kind"] in DOUBT_ITEMS]
    if doubts:  # one line: each doubt is printed in full once, in the appendix
        out.append(f"{_n(len(doubts), 'alternative explanation')} not yet settled, each in full in the appendix "
                   + " ".join(f"[doubt:{i['id']}]" for i in doubts[:5]) + ".")
    if unanswered:
        named = "; ".join(f"{_clause(ths[i['id']].get('about'))} {_open_marker(st, i)}" for i in unanswered[:3])
        more = f"; and {len(unanswered) - 3} more, in the appendix" if len(unanswered) > 3 else ""
        out.append(f"{_n(len(unanswered), 'thread')} raised one level up or about the whole screen left without an "
                   f"answer: {named}{more}.")
    rest = [i for i in items if i not in doubts and i not in unanswered and i not in at_end]
    if rest:
        out.append(f"{_n(len(rest), 'further item')} the record lists as open, each in the appendix "
                   f"{_open_marker(st, rest[0])}.")
    return tops[:max(0, 5 - len(out))] + out


def _build_dossiers(st, jobs_root, out_dir, title, open_items=None, design=None, screen=None):
    """The report of a record whose results are the dossiers the user chose (sessions before item 9 of
    docs/build_goal_session6_fixes.md): each result's panel, its parts as evidence beneath, the supplementary
    figures, methods, what is open and what the record holds."""
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
    cited, lines = Cited(st), [f"# {title}", ""]
    kept = units_kept(st, jobs_root)
    lines += ["## The screen", ""]
    for qid in st["order"]:
        q = st["questions"][qid]
        if q["letter"] in "AB" and q["status"]:
            if q["evidence"]:
                rid = q["evidence"][0]
                text = (f"{q['item'][0].upper()}{q['item'][1:]} ({STATUS_WORDS[q['status']]}): "
                        f"{_clause(_summary(jobs_root, st['runs'][rid]))} [question:{qid}] [run:{rid}].")
            else:
                text = f"{q['item'][0].upper()}{q['item'][1:]}: {_status_words(q)}, {_clause(q['reason'])} " \
                       f"[question:{qid}]."
            lines.append(f"- {render(text, cited)}")
    legend = screen_figure(st, jobs_root, out_dir)  # Figure 1, the overview's, where the record has one
    if legend:
        lines += ["", "![Figure 1](figures/screen.png)", "", render(legend, cited)]
    first = 2 if legend else 1
    lines += ["", "## Results", ""]
    supplementary = []  # (dossier, title, panel, tiles): each result's evidence and limitation tiles
    for i, did in enumerate(st["chosen"], 1):
        d = st["dossiers"][did]
        parts = d["parts"]
        lines += [f"### Result {i}. {render(d['title'], cited)} {render(f'[dossier:{did}]', cited)}", ""]
        panel = parts.get("panel_and_paragraph", {})
        if panel.get("figure") and os.path.exists(panel["figure"]):
            dest = os.path.join("figures", f"result-{i}-{did}.png")
            shutil.copyfile(panel["figure"], os.path.join(out_dir, dest))
            lines += [f"![Figure {i + first - 1}]({dest.replace(os.sep, '/')})", ""]
        tiles = panel.get("tiles") or []
        main = [t for t in tiles if t.get("figure", "main") == "main"]
        lines += [render(_legend(f"Figure {i + first - 1}", d["title"], did, main), cited), ""]
        rest = [t for t in tiles if t.get("figure", "main") != "main"]
        if rest and panel.get("supplementary") and os.path.exists(panel["supplementary"]):
            supplementary.append((did, d["title"], panel["supplementary"], rest))
        if design is not None:
            units, source = _units_and_source(st, d["lead_findings"], kept, screen)
            said, _ = samples_line(None, units, _observations(st, d["lead_findings"]), source,
                                   _main_diagnostics(jobs_root, st, d["main_run"]).get("covariates") or [])
            lines += [f"**Samples.** {render(said, cited)} {render(f'[dossier:{did}]', cited)}", ""]
        if panel.get("text"):
            lines += [render(panel["text"], cited), ""]
        for part, name in (("effect", "Effect"), ("specificity", "Specificity")):
            if part in parts:
                lines += [f"**{name}.** {render(parts[part]['text'], cited)}", ""]
        said = [checks_sentence(fid, st["findings"][fid]) for fid in d["lead_findings"]]
        part = parts.get("checks") or parts.get("robustness")  # a record from before calls it robustness
        checked = " ".join(said + ([part["text"]] if part else []))
        lines += [f"**Checks.** {render(checked, cited)}", ""]
        for part, name in (("convergence", "Convergence"), ("mechanism", "Mechanism (prior knowledge)"),
                           ("relations", "Relations"), ("validation", "Validation")):
            if part in parts:
                lines += [f"**{name}.** {render(parts[part]['text'], cited)}", ""]
    if supplementary:
        lines += ["## Supplementary figures", ""]
        for k, (did, title, path, tiles) in enumerate(supplementary, 1):
            dest = os.path.join("figures", f"supplementary-{k}-{did}.png")
            shutil.copyfile(path, os.path.join(out_dir, dest))
            lines += [f"![Supplementary Figure {k}]({dest.replace(os.sep, '/')})", "",
                      render(_legend(f"Supplementary Figure {k}", title, did, tiles), cited), ""]
    lines += ["## Methods, from the record", ""] + _quality_line(design, st, cited)
    by_module, code = {}, []
    for kind, ident in sorted(cited):
        if kind != "run":
            continue
        module, choices = _module(st["runs"][ident]["what"])
        if module:
            by_module.setdefault((module, choices or ""), []).append(ident)
        else:
            code.append(ident)
    for (module, choices), rids in sorted(by_module.items()):
        how = f"with {choices}" if choices else "with its default choices"
        how += _tested_words(jobs_root, st, rids)
        lines.append("- " + render(f"{module} ran {how} " + " ".join(f"[run:{r}]" for r in rids) + ".", cited))
    for rid in code:  # Pertura's own jobs (a finding's checks, a control-guide null, a block) apart from notebook code
        who = "Pertura" if st["runs"][rid].get("job_id") else "Code in the analysis notebook"
        lines.append("- " + render(f"{who}: {_clause(st['runs'][rid]['what'])} [run:{rid}].", cited))
    for i, did in enumerate(st["chosen"], 1):  # each result's chain back to the deposit (docs/adr/0027, D4)
        lines += [render(x, cited) for x in chain_lines(st, did, i)]
    for did in st["chosen"]:
        for fid in st["dossiers"][did]["lead_findings"]:
            f = st["findings"][fid]
            ran = [CHECK_NAMES[c] for c in CHECK_ORDER if c != "own" and c in f.get("checks", {})
                   and f["checks"][c]["outcome"] != "not run"]
            if ran:
                lines.append("- " + render(f"{finding_name(f, short=True)} was checked by {_listed(ran)}, each "
                                           f"with the lead's own test or the design's other test [finding:{fid}].",
                                           cited))
            tried = ((f.get("checks", {}).get("tests") or {}).get("numbers") or {}).get("rows") or []
            lead = (f.get("checks", {}).get("lead") or {}).get("numbers") or {}
            if tried:  # the tests tried and the one reported (the open points' F5)
                names = [str(r.get("label") or r.get("method")) for r in tried]
                lines.append("- " + render(f"{finding_name(f, short=True)} is reported by "
                                           f"{TEST_WORDS.get(lead.get('test'), lead.get('test') or 'its lead test')}; "
                                           f"also tried: {_listed(names)} [finding:{fid}].", cited))
            if own_measure(st, f):  # the agent's own statistic, not a module's or a block's (docs/adr/0027, D4)
                lines.append("- " + render(f"{finding_name(f, short=True)} was measured by the agent's own code in "
                                           f"the notebook, not by a module or a block [finding:{fid}].", cited))
            if "own" in f.get("checks", {}):
                lines.append("- " + render(f"{finding_name(f, short=True)} was also checked by the agent's own "
                                           f"analysis [finding:{fid}].", cited))
            kept = (st.get("cell_sets") or {}).get(f.get("cells") or "")
            if kept and kept.get("calls"):  # a labeling other than the confirmed calls (ADR 0026, D2)
                lines.append("- " + render(f"{finding_name(f, short=True)} rests on the cell set {f['cells']}, whose "
                                           "labels the agent gave from each cell's called guides, not the confirmed "
                                           f"guide calls [finding:{fid}].", cited))
    used = {f for did in st["chosen"] for f in st["dossiers"][did]["lead_findings"]}
    # the entries these closing sentences link to: the results, else a finding or a run; never a question the
    # analysis has not looked at, since the design's questions are a menu, not a list to close (docs/adr/0032)
    refs = ([f"[dossier:{d}]" for d in st["chosen"]] or [f"[finding:{f}]" for f in list(st["findings"])[:1]]
            or [f"[run:{r}]" for r in list(st["runs"])[:1]])
    holds = " ".join([f"The record holds {_n(len(st['findings']), 'finding')}, {_n(len(st['runs']), 'run')}, "
                      f"{_n(len(st['doubts']), 'doubt')} and {_n(len(st['knowledge']), 'item')} of prior knowledge; "
                      f"this report shows {_n(len(st['chosen']), 'result')}, led by {len(used)} of the findings"]
                     + refs) + "."
    lines += ["", "## Still open", ""]
    items = [i for i in open_items or [] if i["kind"] not in FOR_THE_AGENT]
    lines += [f"- {render(x, cited)}" for x in open_lines(st, items)] or (
        [render("Nothing: every check is done " + " ".join(refs) + ".", cited)]
        if refs else [])
    lines += ["", "## The record", "", render(holds, cited), ""]
    ended = st.get("enough")
    if ended:  # where the user ended the analysis: after which finding, in their words
        before = [fid for fid, f in st["findings"].items() if (f.get("seq") or 0) < ended["seq"]]
        where = [f"[finding:{before[-1]}]"] if before else refs
        lines += [render(f"The user ended the analysis at {ended['time']}, after {_n(len(before), 'finding')} "
                         + " ".join(where) + f": \"{_clause(ended['words'])}\".", cited), ""]
    others = [fid for fid in st["findings"] if fid not in used]
    if others:  # findings the user did not choose as results: named, so a reader knows they exist
        lines += ["### Findings not shown as results", ""]
        lines += ["- " + render(f"{_clause(_finding_line(st['findings'][fid]))} [finding:{fid}].", cited)
                  for fid in others]
        lines.append("")
    answered = doubt_lines(st)
    if answered:
        lines += ["### Doubts answered", ""]
        lines += ["- " + render(x, cited) for x in answered]
        lines.append("")
    lines += [ENTRIES, ""]
    for kind, ident in sorted(cited):
        lines.append(f'<a id="{anchor(kind, ident)}"></a>**{label(kind, ident)}**: {_entry(st, kind, ident)}')
        lines.append("")
    path = os.path.join(out_dir, "report.md")
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)
    return path


# the critic's items that tell the agent what to do next, not what the evidence lacks: kept out of the report
FOR_THE_AGENT = ("research lead due",)


def standing_lines(result):
    """The advisor's standings of a result (docs/adr/0042), one sentence each: the relation, its standing, the
    pathways a new one links, and its sources (the papers, or the searches that found nothing)."""
    advice = result.get("advice") or []
    out = []
    for s in (advice[-1]["standings"] if advice else []):
        links = f"; it would link {_clause(s['links'])}" if s.get("links") else ""
        sources = f" ({'; '.join(s['sources'])})" if s.get("sources") else ""
        out.append(f"{_clause(s['relation'])}: {s['standing']}{links}{sources} [result:{result['result_id']}].")
    return out


SURVEY_LINES = 4  # each comparison's lines of biology in the report; its whole table is beside it


SURVEY_MAPS = ("controls_across", "alike_opposite", "effect_changing", "shifts_per_condition", "states", "proteins")
# drawn in the report beside their rows: the maps of the screen, the conditions' own effect on the control cells among
# them (often its largest axis); the checks (cells, transcripts, guides, units) stay tables


def survey_lines(survey, jobs_root, out_dir, cited, number=None):
    """The report's "The survey" (docs/adr/0045, D5): the design facts it used, then each comparison with its state,
    its run's summary and first lines of biology, and its whole table copied beside the report (survey/<id>.tsv); with
    number, each map's figure (SURVEY_MAPS) beside its row, numbered from number. survey: {facts, rows} as the
    analysis service gives them, or None."""
    from .survey import ROWS
    if not survey or not survey.get("rows"):
        return []
    fx = survey.get("facts") or {}
    used = [("conditions: " + "; ".join(f"{f['name']} ({', '.join(f['levels'])})" for f in fx["factors"]))
            if fx.get("factors") else "no conditions",
            "kinds of control: " + (", ".join(k.replace("_", " ") for k in fx.get("control_kinds") or []) or "none"),
            f"{len(fx.get('samples') or [])} samples"
            + ("" if fx.get("axis_for_samples") or len(fx.get("samples") or []) < 2
               else ", for which the design names no axis"),
            "readouts: genes" + (f" and {fx['antibodies']} antibodies" if fx.get("antibodies") else "")]
    lines = ["", "## The survey", "", "The comparisons the design calls for, run by code beside the analysis's line. "
             "From the design and the screen object: " + "; ".join(used) + ".", ""]
    if survey.get("complete") is False and survey.get("waits"):  # what it waits for (docs/adr/0047, A3)
        lines += [f"The survey has not started, because {survey['waits']}. This section is written again once it has "
                  "run.", ""]
    elif survey.get("complete") is False:  # said until the last table is in (docs/adr/0045, D3)
        lines += [f"The survey is still running: {survey.get('ended', 0)} of {len(survey['rows'])} comparisons have "
                  "ended. This section is written again once they have.", ""]
    folder = os.path.join(out_dir, "survey")
    for r in survey["rows"]:
        png = (os.path.join(jobs_root, r["run_id"], "figure.png")
               if number is not None and r.get("run_id") and r["row"] in SURVEY_MAPS else None)
        res = {}
        if r.get("run_id"):
            try:
                with open(os.path.join(jobs_root, r["run_id"], "result.json"), encoding="utf-8") as fh:
                    res = json.load(fh)
            except (OSError, ValueError):
                res = {}
        said = f"- **{ROWS[r['row']]}**, {r['scope']}: {r['state']}" + (f" ({r['reason']})" if r.get("reason") else "")
        reading = r.get("summary")  # a reading's own summary and table (the samples' baselines), not its run's
        if reading or res.get("summary"):
            said += f". {str(reading or res['summary']).rstrip('.')}"
        lines.append(render(said + (f" [run:{r['run_id']}]" if r.get("run_id") else "") + ".", cited))
        lines += [f"  - {render(str(x), cited)}" for x in ([] if reading else res.get("biology") or [])[:SURVEY_LINES]]
        table = r.get("table") if reading else (
            os.path.join(jobs_root, r["run_id"], "result.tsv") if r.get("run_id") else None)
        if table and os.path.exists(table):
            os.makedirs(folder, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", r["id"]) + ".tsv"
            shutil.copyfile(table, os.path.join(folder, name))
            lines.append(f"  - The whole table: [survey/{name}](survey/{name})")
        if png and os.path.exists(png):  # the map itself: the screen's structure as the comparison drew it
            dest = "figures/survey-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", r["id"]) + ".png"
            os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
            shutil.copyfile(png, os.path.join(out_dir, dest))
            lines += ["", f"![Figure {number}]({dest})", "",
                      render(f"**Figure {number}.** {ROWS[r['row']]}, {r['scope']}, as the survey drew it "
                             f"[run:{r['run_id']}].", cited), ""]
            number += 1
    return lines


def first_look_lines(st, sections, out_dir, number, cited):
    """The report's "First look": each section once (the analysis service's _first_look_sections, in
    its order, a section's runs together), with the statements of the findings that read it, each finding shown once,
    or "not read yet", and the figure of its latest run where Figure 1 does not already draw it (the distances and the
    similar targets are its panels). Returns (lines, figures added); figures are numbered from number."""
    from .candidates import SECTIONS
    if not sections:
        return [], 0
    lines, shown, n = ["", "## First look", ""], set(), 0
    by_section = {}
    for s in sections:
        by_section.setdefault(s["section"], []).append(s)
    for key, group in by_section.items():
        label, _ = SECTIONS[key]
        fids = list(dict.fromkeys(f for s in group for f in s["finding_ids"] if f in st["findings"]))
        new = [f for f in fids if f not in shown]
        shown |= set(new)
        said = (" ".join(f"{_clause(st['findings'][f].get('statement') or _finding_line(st['findings'][f]))}. "
                         f"[finding:{f}]" for f in new)
                or ("Read in the findings above " + " ".join(f"[finding:{f}]" for f in fids) + "." if fids else
                    "Not read yet " + " ".join(f"[run:{s['run_id']}]" for s in group) + "."))
        lines.append(f"- **{label}.** {render(said, cited)}")
        last = group[-1]  # a section's runs in the order they finished: its latest stands for it
        png = last.get("png")
        if png and os.path.exists(png) and key not in ("far_from_controls", "sets_alike"):
            dest = os.path.join("figures", f"first-look-{n + 1}.png")
            shutil.copyfile(png, os.path.join(out_dir, dest))
            lines += ["", f"![Figure {number + n}]({dest.replace(os.sep, '/')})", "",
                      render(f"**Figure {number + n}.** {label}, as the first look drew it [run:{last['run_id']}].",
                             cited), ""]
            n += 1
    return lines, n


def build(st, jobs_root, out_dir, title, open_items=None, design=None, screen=None, screen_dir=None,
          first_look=None, survey=None, open_now=None):
    """Write out_dir/report.md (and its figures) from the record's state; returns its path. open_items: the
    critic's list, stated in the report; design: the confirmed design and screen: the screen object's summary, for each
    result's samples sentence; screen_dir: the screen object's folder, for Figure 1's design where no overview ran;
    first_look: the first look's sections (first_look_lines) and survey: the survey's facts and rows (survey_lines),
    both after the results and the map of the findings; open_now: what the record holds open (stop-facts' lists), for
    the end line."""
    from .record import looked_up, previous_screen_findings, result_facts, result_path, results_shown
    if st.get("chosen") and not results_shown(st):  # a record from before the results: its chosen dossiers
        return _build_dossiers(st, jobs_root, out_dir, title, open_items, design, screen)
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
    cited = Cited(st)
    lines = [f"# {title}", "", render(end_line(st, open_now), cited), ""]  # which end, first (docs/adr/0049)
    kept = units_kept(st, jobs_root)  # each result's n: the units holding cells after quality control
    lines += _story_lines(st, cited)
    lines += ["## The screen", ""]
    legend = screen_figure(st, jobs_root, out_dir)  # Figure 1: the overview's figure and legend stand for the screen
    if legend:
        lines += ["![Figure 1](figures/screen.png)", "", render(legend, cited)]
    else:  # no overview run on this build: each first-look run's summary, under the design's figure
        lines += [f"- {render(x, cited)}" for x in _first_look_lines(st, jobs_root)]
        legend = design_figure(design, screen_dir, out_dir) if design and screen_dir else None
        if legend:
            lines += ["", "![Figure 1](figures/screen.png)", "", legend]
    reading = screen_reading(st, jobs_root)
    if reading:
        lines += [""] + [render(x, cited) for x in reading]
    first = 2 if legend else 1  # the results follow Figure 1; the survey and the first look come after them (the
    # fresh review of 2026-11-13: GSE190604's first result came after nine figures of code's)
    results = results_of(st)
    samples_open = []  # results whose samples sentence does not give their lead tests' count
    undrawn = []  # results with no figure drawn: a run of their findings that saved none stood for it (B1)
    evidence = []  # each result's findings with their checks and doubts, in the evidence file (docs/adr/0047, B7)
    lines += ["", "## Results", ""]
    for i, r in enumerate(results, 1):
        mark = r["marker"]
        name = f"Figure {i + first - 1}"
        lines += [f"### Result {i}. {render(r['title'], cited)} {render(mark, cited)}", ""]
        evidence += [f"### Result {i}. {render(r['title'], cited)} {render(mark, cited)}", ""]
        png = _result_figure(st, jobs_root, r)
        if png and os.path.exists(png):
            dest = os.path.join("figures", f"result-{i}-{r['result_id']}.png")
            shutil.copyfile(png, os.path.join(out_dir, dest))
            lines += [f"![{name}]({dest.replace(os.sep, '/')})", ""]
        if (png and os.path.exists(png)) or r.get("figure") in st["figures"]:  # a figure the record holds
            caption = (st["figures"].get(r.get("figure") or "") or {}).get("caption")
            tiles = [t for t in ((r.get("parts") or {}).get("panel_and_paragraph") or {}).get("tiles") or []
                     if t.get("figure", "main") == "main"]
            legend = ([f"**{name}.** {_clause(r['title'])} {mark}."]
                      + [f"{_clause(x)} [run:{r['figure_run']}]." for x in sentences(caption or "")]
                      + [f"**{t['letter']}**, {_clause(t['caption'])} [run:{t['run_id']}]." for t in tiles]
                      + ([f"Drawn by code from [run:{r['figure_run']}]."] if not tiles else []))
            lines += [render(" ".join(legend), cited), ""]
        else:  # no legend for a figure that is not there
            lines += [render(f"No figure was drawn for this result {mark}.", cited), ""]
            undrawn.append(f"Result {r['result_id']}: no figure was drawn for it {mark}.")
        if design is not None or r.get("samples"):
            units, source = _units_and_source(st, r["finding_ids"], kept, screen)
            said, missing = samples_line(r.get("samples"), units, _observations(st, r["finding_ids"]), source,
                                         _main_diagnostics(jobs_root, st, r["figure_run"]).get("covariates") or [])
            if missing:  # the agent's sentence, checked against the lead tests' units
                samples_open.append(f"Result {r['result_id']}: {missing} {mark}.")
            lines += [f"**Samples.** {render(said, cited)} {render(mark, cited)}", ""]
        runs = [r.get("figure_run")] + [x for fid in r.get("finding_ids") or [] for x in
                                        (st["findings"].get(fid) or {}).get("run_ids") or []]
        tested = tests_sentence(jobs_root, runs)
        if tested:  # what could be tested, beside a result that says something is absent as beside any other
            lines += [f"**Tests.** {render(tested, cited)}", ""]
        if r.get("text"):
            lines += [render(r["text"], cited), ""]
        facts = result_facts(st, r)  # how much of it code checked, said where the result is read
        lines += [f"**Its findings,** {facts['checked']} of {facts['findings']} with their checks run in code, each "
                  "with its checks and doubts in the evidence: " + render(
            " ".join(f"[finding:{fid}]" for fid in r["finding_ids"]), cited) + ".", ""]
        way = result_path(st, r)
        if way["path"] or way["forks"]:  # the analysis as a tree: the steps to this result
            lines += ["**Path.** " + render(_path_sentence(st, way), cited), ""]
        asked = [t for t, th in (st.get("threads") or {}).items() if th.get("next_of") == r["result_id"]]
        if asked:  # docs/adr/0048: where the result's line stopped
            lines += ["**Next question.** " + render(" ".join(_next_question(st, t) for t in asked), cited), ""]
        earlier = previous_screen_findings(st)
        for fid in r["finding_ids"]:
            f = st["findings"][fid]
            mark_ = (" It rests on an earlier build of the screen object, which a rebuild has changed since "
                     f"[finding:{fid}]." if fid in earlier else "")
            own = f.get("statement") or _finding_line(f)  # each finding's own statement, whatever its kind
            evidence.append("- " + render(f"{_clause(own)} [finding:{fid}]. {_checks_words(st, fid)}" + mark_, cited))
        evidence.append("")
        known = [k for k in st["knowledge"] if k.get("finding_id") in r["finding_ids"]]
        if known:
            lines += ["**Prior knowledge beside it.** " + render(" ".join(
                f"{_clause(x)} ({k['source']}{'' if looked_up(st, k) else '; from memory, not looked up'}) "
                f"[knowledge:{k['seq']}]." for k in known
                for x in sentences(k.get("text") or k["item"])), cited), ""]
        differ = [(fid, d) for fid in r["finding_ids"] for d in st["findings"][fid].get("differences") or []]
        if differ:
            lines += ["**Differences from prior work.** " + render(" ".join(
                f"From {d['source']}: {_clause(d['text'])} [finding:{fid}]." for fid, d in differ), cited), ""]
        stands = standing_lines(r)
        if stands:
            lines += ["**Its standing in the literature.** " + render(" ".join(stands), cited), ""]
        for part, label_ in (("convergence", "Convergence"), ("mechanism", "Mechanism (prior knowledge)"),
                             ("relations", "Relations"), ("validation", "Validation")):
            if part in (r.get("parts") or {}):  # a dossier's parts, in a record from before the results
                lines += [f"**{label_}.** {render(r['parts'][part]['text'], cited)}", ""]
    from . import screen_map
    m = screen_map.build(st)
    drawn_map = None
    try:
        drawn_map = screen_map.draw(m, os.path.join(out_dir, "figures", "map.png"))
    except Exception as e:  # said in the report, never silent
        lines += [f"The map of the findings could not be drawn: {type(e).__name__}: {e}", ""]
    if drawn_map:
        name = f"Figure {len(results) + first}"
        behind = sorted({f for r_ in m["relations"] for f in r_["findings"]})
        refs_ = " ".join(f"[finding:{f}]" for f in behind[:6]) or ""
        lines += ["### Map of the findings", "", f"![{name}](figures/map.png)", "",
                  render(f"**{name}.** The screen's objects and the relations its findings show: an arrow raises, a "
                         "bar lowers; solid where seen, dashed where tested and not seen, grey where only prior "
                         f"knowledge gives it {refs_}.", cited), ""]
    after = len(results) + first + (1 if drawn_map else 0)
    surveyed = survey_lines(survey, jobs_root, out_dir, cited, number=after)
    lines += surveyed
    after += sum(x.startswith("![Figure") for x in surveyed)
    lines += first_look_lines(st, first_look, out_dir, after, cited)[0]
    items = [i for i in open_items or [] if i["kind"] not in FOR_THE_AGENT]
    used = {f for r in results for f in r["finding_ids"]}
    refs = ([r["marker"] for r in results] or [f"[finding:{f}]" for f in list(st["findings"])[:1]]
            or [f"[run:{r}]" for r in list(st["runs"])[:1]])
    decided = _decided_for_the_user(st, design)
    if decided:  # what was decided where the user would have (unattended, or "I don't know" on the form)
        lines += ["## Decided for you", ""] + [f"- {render(x, cited)}" for x in decided] + [""]
    lines += ["## Open questions", ""]
    lines += [f"- {render(x, cited)}" for x in _open_questions(st, items)] or (
        [render("Nothing is listed as open " + " ".join(refs) + ".", cited)] if refs else [])
    lines += ["", "## Methods, from the record", ""] + _quality_line(design, st, cited)
    by_module, code = {}, []
    for kind, ident in sorted(cited):
        if kind != "run":
            continue
        module, choices = _module(st["runs"][ident]["what"])
        if module:
            by_module.setdefault((module, choices or ""), []).append(ident)
        else:
            code.append(ident)
    for (module, choices), rids in sorted(by_module.items()):
        how = f"with {choices}" if choices else "with its default choices"
        how += _tested_words(jobs_root, st, rids)
        lines.append("- " + render(f"{module} ran {how} " + " ".join(f"[run:{r}]" for r in rids) + ".", cited))
    for rid in code:  # Pertura's own jobs (a finding's checks, a control-guide null, a block) apart from notebook code
        who = "Pertura" if st["runs"][rid].get("job_id") else "Code in the analysis notebook"
        lines.append("- " + render(f"{who}: {_clause(st['runs'][rid]['what'])} [run:{rid}].", cited))
    for i, r in enumerate(results, 1):  # each result's chain back to the deposit (docs/adr/0027, D4)
        lines += [render(x, cited) for x in chain_lines(st, r["result_id"], i, r["marker"])]
    own = [fid for r in results for fid in r["finding_ids"] if own_measure(st, st["findings"][fid])]
    if own:  # the agent's own statistic, not a module's or a block's (docs/adr/0027, D4), said once (ADR 0047, B1)
        lines.append("- " + render("Measured by the agent's own code in the notebook, not by a module or a block, "
                                   "so code ran no checks on them: " + " ".join(f"[finding:{f}]" for f in own) + ".",
                                   cited))
    for r in results:
        for fid in r["finding_ids"]:
            f = st["findings"][fid]
            kept = (st.get("cell_sets") or {}).get(f.get("cells") or "")
            if kept and kept.get("calls"):  # a labeling other than the confirmed calls (ADR 0026, D2)
                lines.append("- " + render(f"{finding_name(f, short=True)} rests on the cell set {f['cells']}, whose "
                                           "labels the agent gave from each cell's called guides, not the confirmed "
                                           f"guide calls [finding:{fid}].", cited))
    # the report is its results and their figures; the evidence behind them, linked, is a file of its own (B7)
    report, lines = lines, [f"# The evidence for [the report](report.md): {title}", "",
                            "## Each result's findings and their checks", ""]
    lines += evidence
    lines += ["", "## Appendix", "", "### Still open", ""]
    lines += [f"- {render(x, cited)}" for x in _chain_breaks(results) + samples_open + undrawn
              + open_lines(st, items)] or (
        [render("Nothing: every check is done " + " ".join(refs) + ".", cited)] if refs else [])
    answered = doubt_lines(st)
    if answered:
        lines += ["", "### Doubts answered", ""]
        lines += ["- " + render(x, cited) for x in answered]
    others = [fid for fid in st["findings"] if fid not in used]
    if others:  # findings not shown as results: named, so a reader knows they exist
        lines += ["", "### Findings not shown as results", ""]
        lines += ["- " + render(f"{_clause(_finding_line(st['findings'][fid]))} [finding:{fid}].", cited)
                  for fid in others]
    holds = " ".join([f"The record holds {_n(len(st['findings']), 'finding')}, {_n(len(st['runs']), 'run')}, "
                      f"{_n(len(st['doubts']), 'doubt')} and {_n(len(st['knowledge']), 'item')} of prior knowledge; "
                      f"this report shows {_n(len(results), 'result')}, resting on {len(used)} of the findings"]
                     + refs) + "."
    lines += ["", "### The record", "", render(holds, cited), ""]
    ended = st.get("enough")
    if ended:  # where the user ended the analysis: after which finding, in their words
        before = [fid for fid, f in st["findings"].items() if (f.get("seq") or 0) < ended["seq"]]
        where = [f"[finding:{before[-1]}]"] if before else refs
        who = "The analysis ended (unattended)" if ended.get("by_analysis") else "The user ended the analysis"
        lines += [render(f"{who} at {ended['time']}, after {_n(len(before), 'finding')} "
                         + " ".join(where) + f": \"{_clause(ended['words'])}\".", cited), ""]
    lines += [ENTRIES, ""]
    printed = doubts_in_the_appendix(st, items)  # each doubt once: the appendix has it in full
    for kind, ident in sorted(cited):
        lines.append(f'<a id="{anchor(kind, ident)}"></a>**{label(kind, ident)}**: '
                     f'{_entry(st, kind, ident, printed)}')
        lines.append("")
    for name, text in ((EVIDENCE, "\n".join(lines)),  # the entries are here, so the report links into it
                       ("report.md", "\n".join(report).replace("](#", f"]({EVIDENCE}#"))):
        path = os.path.join(out_dir, name)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        os.replace(path + ".tmp", path)
    return path


def _entry(st, kind, ident, printed=()):
    """An entry's line; a doubt among `printed` (the appendix prints it in full) only says what it is on and where
    it stands."""
    if kind == "run":
        r = st["runs"][ident]
        return f"{r['what']}; question {r['question_id']}; {r['state']}; read as: {r.get('reading') or 'no reading'}"
    if kind == "finding":
        f = st["findings"][ident]
        if not f.get("target"):
            return f"{f.get('statement') or 'a finding'}" + (f" (question {f['question_id']})" if f.get("question_id") else "")
        return f"{f['target']} on {f['feature']}, {f['direction']}, in {f['population']} (question {f['question_id']})"
    if kind == "question":
        q = st["questions"][ident]
        return f"{q['item']}: {q['status']}" + (f", evidence {', '.join(q['evidence'])}" if q["evidence"] else "")
    if kind == "dossier":
        d = st["dossiers"][ident]
        return f"{d['title']}; led by {', '.join(d['lead_findings'])}; main run {d['main_run']}"
    if kind == "result":
        r = st["results"][ident]
        said = (f"; {r['decision']} by the analysis, decided for the user (unattended): \"{_clause(r['reason'])}\""
                if r.get("by_analysis") else f"; {r['decision']} by the user: \"{_clause(r['user_words'])}\""
                if r.get("decision") else "; proposed, not yet confirmed by the user")
        return (f"{r['title']}; resting on {', '.join(r['finding_ids'])}; figure from run {r['figure_run']}; "
                f"version {r['version']}" + said)
    if kind == "thread":
        t = st["threads"][ident]
        return f"{t.get('about')}; {t['status']}" + (f": {_clause(t['reason'])}" if t.get("reason") else "")
    if kind == "doubt":
        d = st["doubts"][ident]
        on = (f"on finding {d['finding_id']}" if d.get("finding_id") else
              f"on question {d['question_id']}" if d.get("question_id") else "on the analysis")
        a = d.get("answer")
        if ident in printed:
            return f"a doubt {on}; {'answered ' + a['outcome'] if a else 'not answered'}; in full in the appendix"
        answer = (f"answered {a['outcome']}: {_clause(a['reason'])}"
                  + (f" (runs {', '.join(a['run_ids'])})" if a["run_ids"] else "") if a else "not answered")
        return f"{_clause(d['text'])}; what would change the judgement: {_clause(d['would_change'])}; {on}; {answer}"
    k = next(k for k in st["knowledge"] if str(k["seq"]) == ident)
    return f"{k['source']}: {k['item']}; {k.get('text') or ''} ({k['tag']})"


def check_links(path, st):
    """Every sentence of the report's body links to an entry the record holds, and every list item, which is about one
    entry, links to it once: {sentences, linked, unlinked}, a list item counted as one. The body is the report and its
    evidence file's text before the entries, which the evidence file holds."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    beside = os.path.join(os.path.dirname(path), EVIDENCE)
    if ENTRIES not in text and os.path.exists(beside):
        with open(beside, encoding="utf-8") as fh:
            text += "\n" + fh.read()
    body, _, entries = text.partition(ENTRIES)
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', entries))
    total, missing = 0, []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!["):
            continue
        for sentence in [s[2:]] if s.startswith("- ") else sentences(s):
            total += 1
            ok = [a for a, kind in LINK.findall(sentence) if a in anchors and
                  exists(st, kind, a[len(kind) + 1:])]
            if not ok:
                missing.append(sentence)
    return {"sentences": total, "linked": total - len(missing), "unlinked": missing}
