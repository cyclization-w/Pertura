"""The knowledge base as a page for the user (docs/adr/0032, amendment 2): the same files the agent reads
(skills/pertura-knowledge), with an anchor for every entry, so that the user's page can link each result to the
entries it was read beside.

From the top:
- a search over the entries, in the page itself (words or an id);
- in this project: the entries this project's findings and results were read beside, each with what named it;
- by question: the questions an analysis meets (questions.md), by step, each with its short answer (Pertura's
  reading, a draft for review) and the entries it rests on;
- the base's files, each entry with the questions that rest on it.

Standard library only: it runs on the user's machine, in the plugin's hook. It renders the markdown the base is
written in: headings, paragraphs, lists, tables, bold, italics, code and links; a paradigm card's YAML header is shown
as it is, with an anchor for each step.

    write_if_old(folder, used=None)  ->  the page's path, written again when a file of the base is newer or the
                                         project's entries (used) changed
    titles()                         ->  {id: its title}, from the base's index
    questions()                      ->  the questions, as questions.md gives them
"""
import hashlib
import html
import json
import os
import re

NAME = "knowledge.html"
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "pertura-knowledge")
ID = r"[A-Z]{1,2}\d+(?:\.\d+)?"
HEADING_ID = re.compile(rf"^({ID})\. ")
QUESTIONS = "questions.md"
QUESTION = re.compile(r"^### ([a-z]+(?:-[a-z]+)*)\. (.+?)\s*$")
REF = re.compile(rf"`([A-H]\.[a-z_]+)`|(?<![\w.])({ID})(?![\w.]*\w)")
CHOICES = ("units_and_covariates.md", "references_and_nulls.md", "guides.md", "responses.md",
           "programs_and_gene_sets.md", "effect_sizes.md", "validation_and_stopping.md")
MARK = re.compile(r'<meta name="pertura-project" content="([0-9a-f]*)">')
CITERS = 6  # what named an entry, listed at most, for each entry in this project


def files(base=BASE):
    """The base's files in the order of its index, SKILL.md first."""
    out = ["SKILL.md", QUESTIONS]
    out += [f"choices/{n}" for n in CHOICES if os.path.exists(os.path.join(base, "choices", n))]
    out += [n for n in ("how_judgements_become_choices.md", "pitfalls.md", "follow_up_moves.md", "moments.md",
                        "figure_patterns.md") if os.path.exists(os.path.join(base, n))]
    for sub in ("paradigms", "modules"):
        d = os.path.join(base, sub)
        if os.path.isdir(d):
            out += [f"{sub}/{n}" for n in sorted(os.listdir(d)) if n.endswith(".md")]
    return [f for f in out if os.path.exists(os.path.join(base, f))]


def titles(base=BASE):
    """{id: title} from the index in the base's SKILL.md (its rows "| id | title | file |")."""
    try:
        text = open(os.path.join(base, "SKILL.md"), encoding="utf-8").read()
    except OSError:
        return {}
    text = text[text.find("<!-- index:"):]  # the index's rows, not the tables above it
    out = {}
    for m in re.finditer(r"^\| ([^|]+?) \| ([^|]+?) \| `[^`]+` \|$", text, re.M):
        if m.group(1) != "Id":
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _fields(block):
    """{name: text} of a block's "- **Name:** text" lines; a text runs on over the lines indented under it."""
    out, name = {}, None
    for line in block.splitlines():
        m = re.match(r"^- \*\*([^*]+?):\*\*\s?(.*)$", line)
        if m:
            name = m.group(1).strip()
            out[name] = m.group(2).strip()
        elif name and line.startswith("  ") and line.strip():
            out[name] = (out[name] + " " + line.strip()).strip()
        elif line.strip():
            name = None
    return out


def questions(base=BASE):
    """The questions of questions.md, in order: [{id, title, step, short, sources, read}], read the way
    pertura.knowledge reads them (its "### <id>. <title>" under its step's "## " heading; its short answer; its
    Sources line; the entries its Read line names)."""
    try:
        text = open(os.path.join(base, QUESTIONS), encoding="utf-8").read()
    except OSError:
        return []
    out, step = [], ""
    for block in re.split(r"^(?=#{2,3} )", text, flags=re.M):
        first = block.split("\n", 1)[0]
        m = QUESTION.match(first)
        if m:
            f = _fields(block)
            out.append({"id": m.group(1), "title": m.group(2), "step": step,
                        "short": next((v for k, v in f.items() if k.startswith("Short answer")), ""),
                        "sources": f.get("Sources", ""),
                        "read": list(dict.fromkeys(a or b for a, b in REF.findall(f.get("Read", ""))))})
        elif first.startswith("## "):
            step = first[3:].strip()
    return out


def _link_ids(fragment, known):
    """The known ids in an HTML fragment's text linked to their anchors; a module's name in code too. An id is a
    whole word: "G2/M" names a phase, not G2."""
    parts = re.split(r"(<code>.*?</code>|<a [^>]*>.*?</a>)", fragment)
    for k, part in enumerate(parts):
        if part.startswith("<code>"):
            inner = part[6:-7]
            if inner in known:
                parts[k] = f"<a href=\"#{html.escape(inner)}\">{part}</a>"
        elif not part.startswith("<a "):
            parts[k] = re.sub(rf"(?<![\w.#/-])({ID})(?![\w.]*\w|/)",
                              lambda m: (f"<a class=\"kid\" href=\"#{m.group(1)}\">{m.group(1)}</a>"
                                         if m.group(1) in known else m.group(1)), part)
    return "".join(parts)


def inline(text, known=()):
    """One line of markdown as HTML."""
    s = html.escape(text, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href=\"\2\">\1</a>", s)
    return _link_ids(s, known) if known else s


def _anchor(title, module=None):
    """The anchor of a heading: its entry's id, or a module's name for the module's title line."""
    m = HEADING_ID.match(title)
    if m:
        return m.group(1)
    if module:
        return module
    return None


def _in_questions(eid, gathered):
    """The line under an entry's heading: the questions that rest on it, each linked."""
    links = [f"<a href=\"#{html.escape(q)}\">{inline(t)}</a>" for q, t in gathered.get(eid, [])]
    return f"<p class=\"inq\">Questions it answers: {' · '.join(links)}</p>" if links else ""


def _cell(c, known):
    """A table's cell: an id or a module's name alone is linked to its entry."""
    if c in known:
        return f"<a href=\"#{html.escape(c)}\">{html.escape(c)}</a>"
    return inline(c, known)


def markdown(text, known=(), shift=1, module=None, gathered=None, articles=True):
    """A file of the base as HTML; headings one level down, so that each file sits under the page's title. Each entry
    (a heading with an anchor, to the next heading of its level or above) is an article, which the page's search
    shows or hides, with the questions that rest on it under its heading."""
    out, para, stack, open_at = [], [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para), known) + "</p>")
            para.clear()

    def close_lists():
        while stack:
            out.append(f"</li></{stack.pop()[1]}>")

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        head = re.match(r"^(#{1,6}) (.*)$", line)
        item = re.match(r"^(\s*)(- |\d+\. )(.*)$", line)
        if not s:
            flush()
            close_lists()
        elif head:
            flush()
            close_lists()
            depth = len(head.group(1))
            level = min(6, depth + shift)
            title = head.group(2).strip()
            anchor = _anchor(title, module if depth == 1 else None)
            while open_at and open_at[-1] >= depth:
                out.append("</article>")
                open_at.pop()
            if anchor and articles:
                out.append(f"<article class=\"entry\" data-entry=\"{html.escape(anchor)}\">")
                open_at.append(depth)
            aid = f" id=\"{html.escape(anchor)}\"" if anchor else ""
            out.append(f"<h{level}{aid}>{inline(title)}</h{level}>")
            if anchor and gathered:
                out.append(_in_questions(anchor, gathered))
        elif s.startswith("|"):
            flush()
            close_lists()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            i -= 1
            body = [r for r in rows[1:] if not all(re.fullmatch(r":?-{3,}:?", c) for c in r)]
            cells = ["<div class=\"wrap\"><table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0]) + "</tr>"]
            for r in body:
                cells.append("<tr>" + "".join(f"<td>{_cell(c, known)}</td>" for c in r) + "</tr>")
            out.append("".join(cells) + "</table></div>")
        elif item:
            flush()
            indent, tag = len(item.group(1)), "ul" if item.group(2) == "- " else "ol"
            while stack and stack[-1][0] > indent:
                out.append(f"</li></{stack.pop()[1]}>")
            if stack and stack[-1][0] == indent:
                if stack[-1][1] == tag:
                    out.append("</li>")
                else:
                    out.append(f"</li></{stack.pop()[1]}>")
                    out.append(f"<{tag}>")
                    stack.append((indent, tag))
            else:
                out.append(f"<{tag}>")
                stack.append((indent, tag))
            out.append("<li>" + inline(item.group(3), known))
        elif stack and line.startswith(" "):
            out[-1] += " " + inline(s, known)
        else:
            close_lists()
            para.append(s)
        i += 1
    flush()
    close_lists()
    out += ["</article>"] * len(open_at)
    return "\n".join(out)


def _card(text, known, gathered=None):
    """A paradigm card, as one article: its YAML header shown as it is (each step's id an anchor), then its text."""
    head, body = "", text
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
    pre = re.sub(rf"^(\s*- id: )({ID})$", lambda m: f"{m.group(1)}<span id=\"{m.group(2)}\">{m.group(2)}</span>",
                 html.escape(head.strip("\n"), quote=False), flags=re.M)
    card = re.search(rf"^id: ({ID})$", head, re.M)
    name = re.search(r"^name: (.+)$", head, re.M)
    out = [f"<article class=\"entry\" data-entry=\"{card.group(1) if card else ''}\">"]
    if card:
        out.append(f"<h2 id=\"{card.group(1)}\">{card.group(1)}. {inline(name.group(1)) if name else ''}</h2>")
        out.append(_in_questions(card.group(1), gathered or {}))
    out.append(f"<details><summary>The card's header, with its steps</summary><pre>{pre}</pre></details>")
    out.append(markdown(body.lstrip("\n"), known, shift=2, articles=False))
    out.append("</article>")
    return "\n".join(out)


def _by_question(asked, names, known):
    """The questions by step, each with its short answer and the entries it rests on."""
    e = html.escape
    rows = ["<section id=\"questions\"><h2>By question</h2>",
            "<p class=\"muted\">The questions an analysis meets, by the step of the work. Each short answer is "
            "Pertura's reading of the entries under it, a draft for review; where it and an entry differ, the entry "
            "holds.</p>"]
    contents = {}  # step -> its questions' links, to jump to one
    for q in asked:
        contents.setdefault(q["step"], []).append(f"<a href=\"#{e(q['id'])}\">{inline(q['title'])}</a>")
    rows.append("<div class=\"toc\">" + "".join(f"<p><b>{e(s)}:</b> {' · '.join(links)}</p>"
                                                for s, links in contents.items()) + "</div>")
    step = None
    for q in asked:
        if q["step"] != step:
            step = q["step"]
            rows.append(f"<h3>{e(step)}</h3>")
        chips = [f"<a class=\"chip\" href=\"#{e(i)}\"><span class=\"cid\">{e(i)}</span> {inline(names.get(i, ''))}</a>"
                 if i in known else f"<span class=\"chip\">{e(i)}</span>" for i in q["read"]]
        rows.append(f"<article class=\"question\" id=\"{e(q['id'])}\"><h4>{inline(q['title'])} "
                    f"<span class=\"qid\">{e(q['id'])}</span></h4>"
                    f"<p><span class=\"draft\">Draft</span> {inline(q['short'], known)}</p>"
                    + (f"<p class=\"sources\"><b>Sources:</b> {inline(q['sources'], known)}</p>" if q["sources"] else "")
                    + f"<p class=\"reads\">{''.join(chips)}</p></article>")
    rows.append("</section>")
    return "\n".join(rows)


def _in_project(used, names, known, gathered):
    """The entries this project's findings and results were read beside: each with its questions and what named it
    (each a link to its card on the project's page)."""
    e = html.escape
    rows = ["<section id=\"project\"><h2>Used in this analysis</h2>"]
    if not used:
        rows.append("<p class=\"muted\">No finding or result links to an entry yet. As the analysis goes, the entries they "
                    "link to are listed here.</p></section>")
        return "\n".join(rows)
    rows.append("<p class=\"muted\">The entries this analysis's findings and results link to, with the questions each "
                "answers and where it is linked from, latest first.</p>")
    rows.append("<div class=\"wrap\"><table><tr><th>Entry</th><th>Questions it answers</th><th>Linked from</th></tr>")
    for eid, by in used:
        entry = (f"<a href=\"#{e(eid)}\">{inline(names.get(eid, eid))}</a> <span class=\"cid\">{e(eid)}</span>"
                 if eid in known else e(eid))
        asked = " · ".join(f"<a href=\"#{e(q)}\">{inline(t)}</a>" for q, t in gathered.get(eid, []))
        named = [f"<a href=\"{e(href)}\">{e(label)}</a>" for label, href in list(by)[:CITERS]]
        if len(by) > CITERS:
            named.append(f"and {len(by) - CITERS} more")
        rows.append(f"<tr><td>{entry}</td><td>{asked}</td><td>{'; '.join(named)}</td></tr>")
    rows.append("</table></div></section>")
    return "\n".join(rows)


CSS = """
/* the user's page's palette and fonts (page_style.css), so the two read as one */
:root { --bg: #f2f5f4; --fg: #17201e; --muted: #5f6d6a; --line: #dae2df; --card: #ffffff; --accent: #23787a;
        --chip: #dfeeed;
        --ui: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
        --mono: "Cascadia Mono", Consolas, ui-monospace, "SF Mono", Menlo, monospace; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: #0d1312; --fg: #e1e9e7; --muted: #788784; --line: #22302d; --card: #151d1c; --accent: #6cc3b6;
          --chip: #152f2c; color-scheme: dark; }
}
:root[data-theme="dark"] { --bg: #0d1312; --fg: #e1e9e7; --muted: #788784; --line: #22302d; --card: #151d1c; --accent: #6cc3b6;
                          --chip: #152f2c; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 64px; background: var(--bg); color: var(--fg);
       font: 15px/1.55 var(--ui); -webkit-font-smoothing: antialiased; }
main { max-width: 980px; margin: 0 auto; overflow-wrap: anywhere; }  /* long ids and paths break on a phone */
main table { overflow-wrap: break-word; }
h1 { font-size: 30px; font-weight: 600; letter-spacing: -.015em; margin: 0 0 6px; }
h2 { font-size: 19px; margin: 40px 0 10px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }
h3 { font-size: 16px; margin: 26px 0 8px; }
h4, h5 { font-size: 15px; margin: 18px 0 6px; }
:target, article.question:target { background: var(--chip); border-radius: 4px; }
section { border-top: 1px solid var(--line); margin-top: 28px; }
.muted { color: var(--muted); font-size: 13px; }
code { font: 13px/1.4 var(--mono); background: var(--card); padding: 0 3px; border-radius: 3px; }
pre { font: 12.5px/1.45 var(--mono); background: var(--card); padding: 10px 12px; overflow-x: auto;
      border-radius: 6px; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--line); }
th { font-weight: 600; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 2px; }
a.kid { text-decoration: none; background: var(--chip); padding: 0 5px; border-radius: 999px; font-size: 13px; }
details summary { cursor: pointer; color: var(--muted); font-size: 13px; }
.search { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
          padding: 10px 0; background: var(--bg); border-bottom: 1px solid var(--line); }
.search input { flex: 1 1 260px; min-width: 0; font: inherit; padding: 7px 10px; color: var(--fg);
                background: var(--card); border: 1px solid var(--line); border-radius: 8px; }
nav { margin: 10px 0 0; }
article.question { margin: 0 0 12px; padding: 12px 14px; background: var(--card); border: 1px solid var(--line);
                   border-radius: 10px; }
article.question h4 { margin: 0 0 6px; font-size: 15.5px; }
article.question p { margin: 0 0 6px; }
.qid, .cid { font: 12px/1.4 var(--mono); color: var(--muted); }
.draft { margin-right: 4px; padding: 0 6px; font-size: 12px; color: var(--muted); white-space: nowrap;
         border: 1px solid var(--line); border-radius: 999px; }
.reads { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 2px 8px; font-size: 13px; text-decoration: none; background: var(--chip); border-radius: 999px; }
.toc { margin: 0 0 18px; font-size: 13.5px; }
.toc p { margin: 0 0 6px; }
.toc a { text-decoration: none; }
.inq { margin: -2px 0 10px; font-size: 13px; color: var(--muted); }
.inq a { text-decoration: none; }
body.searching section:not(.has-hit), body.searching section > :not(article),
body.searching article:not(.hit) { display: none; }
"""

# the search: each entry and question is an article; with words typed, the page shows the articles that hold all of
# them and hides the rest
SCRIPT = """
(function () {
  var q = document.getElementById("q"), hits = document.getElementById("hits");
  var items = [].slice.call(document.querySelectorAll("article"));
  var text = items.map(function (a) { return a.textContent.toLowerCase(); });
  var sections = [].slice.call(document.querySelectorAll("section"));
  function run() {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean), n = 0;
    document.body.classList.toggle("searching", words.length > 0);
    items.forEach(function (a, k) {
      var hit = words.length > 0 && words.every(function (w) { return text[k].indexOf(w) >= 0; });
      a.classList.toggle("hit", hit);
      if (hit) { n += 1; }
    });
    sections.forEach(function (s) { s.classList.toggle("has-hit", !!s.querySelector("article.hit")); });
    hits.textContent = words.length ? (n === 1 ? "1 entry holds it" : n + " entries hold it") : "";
  }
  q.addEventListener("input", run);
})();
"""


def _mark(used):
    """A short fingerprint of the project's entries, kept in the page to tell when they changed."""
    if used is None:
        return ""
    return hashlib.sha1(json.dumps(used, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def render(base=BASE, used=None):
    """The page's HTML: the search, the entries this project read (used: [(id, [(label, href)])], or None for the
    base alone), the questions, then every file, each entry with its anchor and the questions that rest on it."""
    names = titles(base)
    asked = questions(base)
    words = {q["id"] for q in asked}
    known = set(names) - words  # ids linked where a text names them; a question is linked by the page's own links
    steps = {}  # a paradigm card's step -> what it does
    for rel in files(base):
        if rel.startswith("paradigms/"):
            head = open(os.path.join(base, rel), encoding="utf-8").read()
            known |= set(re.findall(rf"^\s*- id: ({ID})$", head, re.M))
            steps.update(re.findall(rf"^\s*- id: ({ID})\n\s+does: (.+)$", head, re.M))
    gathered = {}  # an entry's id -> the questions that rest on it
    for q in asked:
        for i in q["read"]:
            gathered.setdefault(i, []).append((q["id"], q["title"]))
    nav = (["<a href=\"page.html\">Back to the analysis</a>",  # the project's page beside it (page.PAGE_NAME)
            "<a href=\"#project\">Used in this analysis</a>"] if used is not None else []) + [
        "<a href=\"#questions\">By question</a>", "<a href=\"#file-SKILL.md\">The index</a>"]
    rows = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
            f"<meta name=\"pertura-project\" content=\"{_mark(used)}\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            "<title>Pertura's knowledge base</title><script>try{var t=localStorage.getItem('pertura-theme');"  # the
            "if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>"  # page's light or dark
            f"<style>{CSS}</style></head><body><main>",
            "<h1>Pertura's knowledge base</h1>",
            "<p class=\"muted\">The entries the analysis agent reads, from published screens and methods papers. A "
            "result on your page names the entries it was read beside; each links here.</p>",
            "<div class=\"search\"><input id=\"q\" type=\"search\" placeholder=\"Search the entries: words or an id\" "
            "aria-label=\"Search the entries\"><span id=\"hits\" class=\"muted\"></span></div>",
            f"<nav class=\"muted\">{' · '.join(nav)}</nav>"]
    if used is not None:
        rows.append(_in_project(used, names, known | words, gathered))
    rows.append(_by_question(asked, {**steps, **names}, known))
    for rel in files(base):
        if rel == QUESTIONS:
            continue
        text = open(os.path.join(base, rel), encoding="utf-8").read()
        rows.append(f"<section id=\"file-{html.escape(rel)}\"><p class=\"muted\">{html.escape(rel)}</p>")
        if rel == "SKILL.md":
            text = text.split("\n---", 1)[1] if text.startswith("---") else text
            text = re.sub(r"<!--.*?-->", "", text)
            text = re.sub(r"^# .*$", "", text, count=1, flags=re.M)
            rows.append(markdown(text, known | words))
        elif rel.startswith("paradigms/"):
            rows.append(_card(text, known, gathered))
        else:
            module = os.path.basename(rel)[:-3] if rel.startswith("modules/") else None
            rows.append(markdown(text, known, module=module, gathered=gathered))
        rows.append("</section>")
    rows.append(f"</main><script>{SCRIPT}</script></body></html>")
    return "\n".join(rows) + "\n"


def _mark_in(path):
    """The fingerprint a written page holds, or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            m = MARK.search(fh.read(2000))
    except OSError:
        return None
    return m.group(1) if m else None


def write_if_old(folder, base=BASE, used=None):
    """The page in the folder, written again when it is missing, a file of the base is newer, or the project's
    entries (used, as render takes them) changed; None where the base is not beside the plugin."""
    if not os.path.isdir(base):
        return None
    path = os.path.join(folder, NAME)
    newest = max(os.path.getmtime(os.path.join(base, f)) for f in files(base))
    if os.path.exists(path) and os.path.getmtime(path) >= newest and _mark_in(path) == _mark(used):
        return path
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(base, used))
    os.replace(tmp, path)
    return path
