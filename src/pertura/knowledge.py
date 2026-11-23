"""The knowledge base (docs/adr/0032, amendment 2): the field's knowledge in one folder,
plugin/skills/pertura-knowledge, which the agent and the user both read and the results point to.

Every entry has an id:
- a question an analysis meets (questions.md), "### <id>. <title>" under its step's heading, whose id is a short
  word ("units", "guide-calls"): its short answer (Pertura's reading, a draft for a person's review) and the entries
  it rests on;
- a heading "<id>. <title>" (## or ###) in the base's files: the cards on analysis choices and how judgements
  become choices, the pitfalls, the follow-up moves, the tools' moments and the figure patterns;
- a paradigm card (the id of its YAML header) and each of its steps;
- a module's entry, modules/<module>.md, whose id is the module's name.

    base = load()
    base.get("PF9"); base.read(["G3", "P6.4"]); base.find("shared response")
    base.get("units")["read"]          # the entries a question rests on; base.gathered["PF2"]: the questions
    base.get("B.efficacy")["ways"]     # each way the module can mislead: its shows_in, read and do
    python -m pertura.knowledge index        # the index, as SKILL.md holds it
    python -m pertura.knowledge write-index  # SKILL.md's index made again from the files
"""
import os
import re
import sys

ID = r"[A-Z]{1,2}\d+"
HEADING = re.compile(rf"^(#{{2,3}}) ({ID})\. (.+?)\s*$", re.M)
ANY_HEADING = re.compile(r"^(#{1,6}) ", re.M)
IDS_IN = re.compile(rf"(?<![\w.])({ID}(?:\.\d+)?)(?![\w.]*\w)")
FIELD = re.compile(r"^- \*\*([^*]+?):\*\*\s?(.*)$")
NO_KNOWLEDGE = "no knowledge yet"
QUESTIONS = "questions.md"
QUESTION_HEADING = re.compile(r"^### ([a-z]+(?:-[a-z]+)*)\. (.+?)\s*$", re.M)
STEP_HEADING = re.compile(r"^## (.+?)\s*$", re.M)
SHORT = "Short answer"
SOURCES = "Sources"
READING = ("the short answer is a draft for a person's review, drawn from the entries it names; its Sources line names "
           "the papers behind it and what is Pertura's choice; where it and an entry differ, the entry holds")
REF = re.compile(rf"`([A-H]\.[a-z_]+)`|(?<![\w.])({ID}(?:\.\d+)?)(?![\w.]*\w)")
KINDS = (("choices/", "card on an analysis choice"),
         ("how_judgements_become_choices.md", "how judgements become choices"), ("pitfalls.md", "pitfall"),
         ("follow_up_moves.md", "follow-up move"), ("moments.md", "moment"), ("figure_patterns.md", "figure pattern"))
# the cards on analysis choices, in the order of the index
CHOICES = ("units_and_covariates.md", "references_and_nulls.md", "guides.md", "responses.md",
           "programs_and_gene_sets.md", "effect_sizes.md", "validation_and_stopping.md")
INDEX_START, INDEX_END = "<!-- index: ", "<!-- end of index -->"
READ_CAP = 12000  # characters read_knowledge returns at most; the rest is named, to read by id


class KnowledgeError(Exception):
    pass


def folder():
    """The base's folder: PERTURA_KNOWLEDGE, else the plugin's, two levels above this package."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("PERTURA_KNOWLEDGE") or os.path.normpath(
        os.path.join(here, "..", "..", "plugin", "skills", "pertura-knowledge"))


def ids_in(text):
    """The ids a text names, in order, once each ("G3, PF7" -> ["G3", "PF7"])."""
    return list(dict.fromkeys(IDS_IN.findall(text or "")))


def refs(text):
    """The entries a list names, ids and modules' names in backticks, in order, once each
    ("PF2, U1, `C.genes`" -> ["PF2", "U1", "C.genes"])."""
    return list(dict.fromkeys(a or b for a, b in REF.findall(text or "")))


def citations(text):
    """The groups of entries a text cites: each parenthesis that holds only entries ("(PF2, U1, `C.genes`)"), as
    [(where it ends, [its entries])]; a parenthesis of words ("(mouse, donor)") cites nothing."""
    out = []
    for m in re.finditer(r"\(([^()]*)\)", text or ""):
        items = [s.strip() for s in m.group(1).split(",")]
        if items and all(REF.fullmatch(s) for s in items):
            out.append((m.end(), refs(m.group(1))))
    return out


def fields(block):
    """{name: text} of a block's "- **Name:** text" lines. A text runs on over the lines indented under it; a field's
    own items ("  - ...") are listed under "<name> items"."""
    out, name = {}, None
    for line in block.splitlines():
        m = FIELD.match(line)
        if m:
            name = m.group(1).strip()
            out[name] = m.group(2).strip()
        elif name and line.startswith("  ") and line.strip():
            s = line.strip()
            items = f"{name} items"
            if s.startswith("- "):
                out.setdefault(items, []).append(s[2:])
            elif out.get(items):
                out[items][-1] += " " + s
            else:
                out[name] = (out[name] + " " + s).strip()
        elif line.strip():
            name = None
    return out


def _sections(text):
    """(id, title, text) of every heading with an id; a section runs to the next heading of its level or above."""
    starts = [(len(m.group(1)), m.start()) for m in ANY_HEADING.finditer(text)]
    out = []
    for m in HEADING.finditer(text):
        level = len(m.group(1))
        end = next((s for lv, s in starts if s > m.start() and lv <= level), len(text))
        out.append((m.group(2), m.group(3), text[m.start():end].strip()))
    return out


def _parts(text, level=2):
    """{heading: body} of a text's headings of one level."""
    out = {}
    for piece in re.split(rf"^{'#' * level} ", text, flags=re.M)[1:]:
        title, _, body = piece.partition("\n")
        out[title.strip()] = body
    return out


def _module_entry(path, rel):
    """A module's entry: its title line "# <module>: <its title>", its parts, and the ways its result can mislead,
    each a "### <the contract's words>" with Shows in, Read and Do."""
    text = open(path, encoding="utf-8").read()
    first = text.splitlines()[0] if text else ""
    name, _, title = first.lstrip("# ").partition(": ")
    parts = _parts(text)
    ways = []
    for way, body in _parts(parts.get("Ways the result can mislead", ""), level=3).items():
        f = fields(body)
        read = f.get("Read", "")
        ways.append({"way": way, "shows_in": f.get("Shows in", ""), "read": ids_in(read), "read_text": read,
                     "do": f.get("Do", "")})
    calls = fields(parts.get("Moves it calls for", ""))
    return {"id": name.strip(), "title": title.strip(), "kind": "module", "file": rel, "text": text.strip(),
            "ways": ways, "calls_for": {k: v for k, v in calls.items() if not k.endswith(" items")},
            "also_read": ids_in(parts.get("Further reading", "")), "follows": parts.get("Next steps", "").strip()}


def _card_entries(path, rel):
    """A paradigm card and its steps, from its YAML header; a step's text has its fields and the card's paragraphs
    on it ("**P6.2.** ...")."""
    from . import paradigms
    card = paradigms.read(path)
    body = open(path, encoding="utf-8").read().split("\n---", 1)[-1]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    steps = card.get("steps") or []
    lines = [f"{card['id']}. {card.get('name', '')} ({rel})", f"Use when: {card.get('use_when', '')}".strip()]
    if card.get("not_when"):
        lines.append(f"Not when: {card['not_when']}")
    if card.get("requires"):
        lines.append(f"Requires: {', '.join(card['requires'])}")
    lines.append("Steps:")
    lines += [f"- {s.get('id')} {s.get('does', '')} [{s.get('part', '')}]" for s in steps]
    lines.append("Each step's controls, forks and papers: read_knowledge with the step's id, or the card's file.")
    out = [{"id": card["id"], "title": card.get("name", ""), "kind": "paradigm card", "file": rel,
            "text": "\n".join(lines)}]
    for s in steps:
        sid = s.get("id")
        own = [p for p in paragraphs if re.match(r"\*\*[^*]*\*\*", p)
               and re.search(rf"(?<![\w.]){re.escape(sid)}(?![\w.]*\w)", re.match(r"\*\*([^*]*)\*\*", p).group(1))]
        text = [f"{sid}. {s.get('does', '')} (card {card['id']}, {card.get('name', '')}; {rel})",
                f"Part: {s.get('part', '')}"]
        for key, label in (("control", "Control"), ("fork", "Fork"), ("fork_default", "The card's choice"),
                           ("when", "When")):
            if s.get(key):
                text.append(f"{label}: {s[key]}")
        if s.get("fork_key"):
            text.append("A key fork: the user's to decide.")
        out.append({"id": sid, "title": s.get("does", ""), "kind": "card step", "file": rel,
                    "text": "\n".join(text + own)})
    return out


def _question_entries(path, rel):
    """The questions of questions.md: each "### <id>. <title>" under its step's "## " heading, with its short answer
    and the entries its Read line names."""
    text = open(path, encoding="utf-8").read()
    steps = [(m.start(), m.group(1)) for m in STEP_HEADING.finditer(text)]
    starts = [m.start() for m in ANY_HEADING.finditer(text)]
    out = []
    for m in QUESTION_HEADING.finditer(text):
        end = next((s for s in starts if s > m.start()), len(text))
        block = text[m.start():end].strip()
        f = fields(block)
        out.append({"id": m.group(1), "title": m.group(2), "kind": "question", "file": rel, "text": block,
                    "step": next((name for at, name in reversed(steps) if at < m.start()), ""),
                    "short": f.get(SHORT, ""), "sources": f.get(SOURCES, ""), "read": refs(f.get("Read", ""))})
    return out


class Base:
    def __init__(self, root, entries):
        self.root = root
        self.entries = entries  # id -> entry, in the order of the index
        self.gathered = {}  # an entry's id -> the questions that rest on it
        for qid, q in self.questions().items():
            for i in q["read"]:
                self.gathered.setdefault(i, []).append(qid)

    def get(self, eid):
        return self.entries.get(eid)

    def questions(self):
        return {k: e for k, e in self.entries.items() if e["kind"] == "question"}

    def read(self, ids):
        """The entries of these ids, and the ids none holds."""
        found = [self.entries[i] for i in ids if i in self.entries]
        return found, [i for i in ids if i not in self.entries]

    def modules(self):
        return {k: e for k, e in self.entries.items() if e["kind"] == "module"}

    def moments(self):
        """{id: {tool, arg, value, read, why, title}} from moments.md's entries: Applies at names the tool in
        backticks, and where the moment is one kind of call, the argument and its value ("`record_finding`, kind
        `custom`"), or `given` for any call that gives the argument; The reason says why it is read there."""
        out = {}
        for eid, e in self.entries.items():
            if e["kind"] != "moment":
                continue
            f = fields(e["text"])
            m = re.match(r"`([\w.]+)`(?:, (\w+) `([^`]+)`)?", f.get("Applies at", ""))
            out[eid] = {"tool": m.group(1) if m else None, "arg": m.group(2) if m else None,
                        "value": m.group(3) if m else None, "read": ids_in(f.get("Read", "")),
                        "why": f.get("The reason", ""), "title": e["title"]}
        return out

    def find(self, words, limit=12, questions=False):
        """The entries that hold the words, best first: [(id, title, kind)]. A word in the title counts three times.
        questions: the questions that hold them, in place of the other entries."""
        terms = [w for w in re.findall(r"[a-z0-9]+", (words or "").lower()) if len(w) > 2 or w.isdigit()]
        scored = []
        for eid, e in self.entries.items():
            if (e["kind"] == "question") != questions:
                continue
            title, text = (eid + " " + e["title"]).lower(), e["text"].lower()
            score = sum(3 * title.count(t) + text.count(t) for t in terms)
            if terms and all(t in title or t in text for t in terms):
                score *= 2
            if score:
                scored.append((-score, eid))
        return [(eid, self.entries[eid]["title"], self.entries[eid]["kind"]) for _, eid in sorted(scored)[:limit]]

    def index(self):
        """The index as SKILL.md holds it: one row per entry, a card's steps under their card."""
        rows = ["| Id | Entry | File |", "|---|---|---|"]
        for eid, e in self.entries.items():
            if e["kind"] == "card step":
                continue
            title = e["title"].replace("|", "/") + (_run_by(eid) if e["kind"] == "module" else "")
            rows.append(f"| {eid} | {title} | `{e['file']}` |")
        return "\n".join(rows)


def _run_by(module):
    """A module entry's tool calls in the index (docs/adr/0044, D4: the module entries sit under the tools)."""
    from .kinds import places
    calls = [f"`{t}`" + (f" view `{v}`" if v != t else "") for t, v in places(module)]
    return f" (run by {'; '.join(calls)})" if calls else " (run by code, in the first look)"


def _order(root):
    """The base's files in the order of the index, the questions first."""
    files = [QUESTIONS] if os.path.exists(os.path.join(root, QUESTIONS)) else []
    files += [os.path.join("choices", n) for n in CHOICES if os.path.exists(os.path.join(root, "choices", n))]
    if os.path.isdir(os.path.join(root, "choices")):
        files += sorted(os.path.join("choices", n) for n in os.listdir(os.path.join(root, "choices"))
                        if n.endswith(".md") and n not in CHOICES)
    files += [n for n in ("how_judgements_become_choices.md", "pitfalls.md", "follow_up_moves.md", "moments.md",
                          "figure_patterns.md") if os.path.exists(os.path.join(root, n))]
    for sub in ("paradigms", "modules"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            files += sorted(os.path.join(sub, n) for n in os.listdir(d) if n.endswith(".md"))
    return [f.replace(os.sep, "/") for f in files]


_LOADED = {}  # folder -> (its files' times, the base): read again only when a file changes


def load(root=None):
    """The base: every entry by id; KnowledgeError when an id is held twice."""
    root = root or folder()
    stamp = tuple((rel, os.path.getmtime(os.path.join(root, rel))) for rel in _order(root))
    if root in _LOADED and _LOADED[root][0] == stamp:
        return _LOADED[root][1]
    base = _read(root)
    _LOADED[root] = (stamp, base)
    return base


def _read(root):
    entries, twice = {}, []

    def add(e):
        if e["id"] in entries:
            twice.append(f"{e['id']} ({entries[e['id']]['file']} and {e['file']})")
        else:
            entries[e["id"]] = e

    for rel in _order(root):
        path = os.path.join(root, rel)
        if rel == QUESTIONS:
            for e in _question_entries(path, rel):
                add(e)
        elif rel.startswith("paradigms/"):
            for e in _card_entries(path, rel):
                add(e)
        elif rel.startswith("modules/"):
            add(_module_entry(path, rel))
        else:
            kind = next(k for prefix, k in KINDS if rel.startswith(prefix))
            for eid, title, text in _sections(open(path, encoding="utf-8").read()):
                add({"id": eid, "title": title, "kind": kind, "file": rel, "text": text})
    if twice:
        raise KnowledgeError("ids held twice: " + "; ".join(twice))
    return Base(root, entries)


def read_reply(ids=None, module=None, words=None, base=None, cap=READ_CAP):
    """read_knowledge's reply: the entries of the ids, or a module's entry, whole while they fit in the cap (the rest
    named, to read by id), a question with the titles of the entries it rests on; for words, the questions and the
    entries that hold them; with nothing asked, what the base holds and its questions by step."""
    base = base or load()
    ask = list(ids or [])
    if module:
        from .question_modules.contract import RENAMED
        ask.insert(0, RENAMED.get(module, module))
    if not ask and not words:
        kinds, steps = {}, {}
        for e in base.entries.values():
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        for qid, q in base.questions().items():
            steps.setdefault(q["step"], []).append([qid, q["title"]])
        out = {"holds": kinds, "folder": base.root}
        if steps:
            out["questions"] = steps
        out["how"] = ("ids: a question's id (units, calibration) gives its short answer and the entries it rests on; "
                      "entries by their ids (R3, PF9, T3, P6.4, a module's name); module: a module's entry; words: "
                      "the questions and entries that hold them. The index of every id is the folder's SKILL.md")
        return out
    out = {}
    if ask:
        found, missing = base.read(ask)
        entries, used, left = [], 0, []
        for e in found:
            if entries and used + len(e["text"]) > cap:
                left.append(e["id"])
                continue
            one = {"id": e["id"], "title": e["title"], "kind": e["kind"], "file": e["file"], "text": e["text"]}
            if e["kind"] == "question":
                one["step"] = e["step"]
                one["rests_on"] = [{"id": i, "title": base.get(i)["title"], "kind": base.get(i)["kind"]}
                                   for i in e["read"] if base.get(i)]
                out["reading"] = READING
                out["how"] = "read_knowledge with the ids a question rests on gives each entry"
            entries.append(one)
            used += len(e["text"])
        out["entries"] = entries
        if left:
            out["left_out"] = left
            out["next"] = "the entries left out fit in another call: read_knowledge with their ids"
        if missing:
            out["no_entry"] = missing
    if words:
        asked = [{"id": i, "title": title} for i, title, _ in base.find(words, limit=5, questions=True)]
        if asked:
            out["questions"] = asked
        out["found"] = [{"id": i, "title": title, "kind": kind} for i, title, kind in base.find(words)]
    return out


def write_index(root=None):
    """SKILL.md's index made again from the files; returns whether it changed."""
    root = root or folder()
    path = os.path.join(root, "SKILL.md")
    text = open(path, encoding="utf-8").read()
    start, end = text.index(INDEX_START), text.index(INDEX_END)
    head = text[start:text.index("\n", start) + 1]
    new = text[:start] + head + "\n" + load(root).index() + "\n\n" + text[end:]
    if new == text:
        return False
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    os.replace(path + ".tmp", path)
    return True


def index_in_skill(root=None):
    """The index SKILL.md holds now, as written between its markers."""
    text = open(os.path.join(root or folder(), "SKILL.md"), encoding="utf-8").read()
    start, end = text.index(INDEX_START), text.index(INDEX_END)
    return text[text.index("\n", start) + 1:end].strip()


def main(argv):
    if argv[:1] == ["index"]:
        print(load().index())
    elif argv[:1] == ["write-index"]:
        print("changed" if write_index() else "unchanged")
    else:
        raise SystemExit("python -m pertura.knowledge index | write-index")


if __name__ == "__main__":
    main(sys.argv[1:])
