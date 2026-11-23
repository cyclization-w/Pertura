"""The knowledge base as a documentation site (scripts/knowledge_base_explorer.py): on a made-up base, a page for every
question, step, entry and section, each with what the base gives it (a question's short answer, sources and entries;
an entry's kind, text, sources and questions; the moves by kind with the modules that call for them), the sidebar,
the home page's counts, the old links, a page that stands alone, written whole; on the plugin's base, every entry,
question and move has its page, and the committed page was built from this base."""
import importlib.util
import json
import os
import re
import tempfile
import unittest

from pertura import knowledge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "knowledge_base_explorer.py")
if not os.path.exists(SCRIPT):  # a release folder holds no scripts/: the site is built from a checkout
    raise unittest.SkipTest("scripts/knowledge_base_explorer.py is not in this folder")
_spec = importlib.util.spec_from_file_location("knowledge_base_explorer", SCRIPT)
kbx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kbx)
COMMITTED = os.path.join(ROOT, "knowledge_base_explorer", "index.html")

FILES = {
    "questions.md": """# The questions an analysis meets

## The design

### units. The unit an effect is tested over

- **Short answer:** Test over the mice, not the cells (PF1, `C.genes`).
- **Sources:** Squair et al. 2021 (pseudobulk by replicate); Pertura's choice: mice by default.
- **Read:** PF1, `C.genes`

### calls. How each cell's guides are called

- **Short answer:** Call the guides with a mixture model (PF1).
- **Sources:** Pertura's choice: one call per cell.
- **Read:** PF1

## Effects

### genes. The genes a target changes

- **Short answer:** Compare pseudobulk by mouse, then check each guide (T1).
- **Sources:** Doe et al. 2020 (each guide alone).
- **Read:** T1, `C.genes`
""",
    "pitfalls.md": """# Pitfalls

Mistakes that make a finding look better than it is.

## Samples and cells

### PF1. Say what the n is

Mice are the replicates; lanes are not.
- **Source:** Squair et al., Nat Commun 2021,
  doi:10.1038/s41467-021-25960-2.
""",
    "follow_up_moves.md": """# Follow-up moves: the next step a result calls for

The moves published screens made.

## Trust the result

### T1. Guides agree

- **Worth considering when:** a target has two or more guides.
- **Run:** `F.guides_agree`.
- **Published screens:**
  - Doe 2020 (Fig. 1a): the guides clustered by their effects

## Organise the effects

### O1. Programs

- **Worth considering when:** many targets change genes.
- **Run:** `C.programs`.

## One way through a lead

1. Read the result.
""",
    "modules/C.genes.md": """# C.genes: The genes each target changes

**The biology.** A target's changed genes, tested over its replicates (PF1).

## Moves it calls for

- **T1:** worth considering when a target has two guides
- **O1:** worth considering when 3 or more targets change genes
""",
}


def write_base(root):
    for rel, text in FILES.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def pages(html_text):
    """{route: the page's HTML}, each page running to the next one or to the footer."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"<div class=\"page\" data-route=\"([^\"]+)\"", html_text)]
    end = html_text.index("<footer class=\"site-foot\"")
    return {route: html_text[at:(starts[k + 1][0] if k + 1 < len(starts) else end)]
            for k, (at, route) in enumerate(starts)}


def data(html_text):
    return json.loads(re.search(r"id=\"kb-data\">(.*?)</script>", html_text, re.S).group(1))


def links(fragment):
    return re.findall(r"href=\"#(/[^\"]*)\"", fragment)


def part(page_html, cls):
    """The HTML of a page's part (content, after, related), up to the next part."""
    m = re.search(rf"<div class=\"{cls}[^\"]*\"[^>]*>(.*?)(?=<div class=\"(?:content|after|related)|<nav class=\"pager|$)",
                  page_html, re.S)
    return m.group(1) if m else ""


class TestAMadeUpBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = os.path.join(cls.tmp.name, "pertura-knowledge")
        write_base(cls.base)
        cls.html = kbx.render(cls.base, commit="abc1234", built="2026-10-29")
        cls.pages = pages(cls.html)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_question_step_entry_and_section_has_its_page(self):
        self.assertEqual(set(self.pages), {
            "/", "/questions", "/questions/step/design", "/questions/step/effects", "/questions/units",
            "/questions/calls", "/questions/genes", "/moves", "/moves/T1", "/moves/O1", "/modules", "/modules/C.genes",
            "/pitfalls", "/entries/PF1", "/404"})
        self.assertEqual(data(self.html)["routes"], {
            "units": "/questions/units", "calls": "/questions/calls", "genes": "/questions/genes",
            "PF1": "/entries/PF1", "T1": "/moves/T1", "O1": "/moves/O1", "C.genes": "/modules/C.genes"})
        self.assertEqual([r for r in set(links(self.html)) if r.split("?")[0] not in self.pages], [])

    def test_a_question_page_gives_its_short_answer_sources_and_entries(self):
        p = self.pages["/questions/units"]
        self.assertIn("<h1>The unit an effect is tested over</h1>", p)
        crumbs = re.search(r"<nav class=\"crumbs\".*?</nav>", p, re.S).group(0)
        self.assertEqual(re.findall(r">([^<>/]+)</(?:a|span)>", crumbs),
                         ["Home", "Questions", "Design", "The unit an effect is tested over"])
        content = part(p, "content")
        self.assertRegex(content, r"<h2 id=\"[^\"]+\">Short answer</h2><p><span class=\"draft\">Draft</span> Test over "
                                  r"the mice, not the cells")
        self.assertRegex(content, r"<h2 id=\"[^\"]+\">Sources</h2><p>Squair et al. 2021 \(pseudobulk by replicate\); "
                                  r"Pertura's choice: mice by default.</p>")
        self.assertEqual(links(part(p, "after")), ["/entries/PF1", "/modules/C.genes"])
        related = part(p, "related")
        self.assertEqual(links(related), ["/questions/calls", "/questions/genes"])  # each shares one entry
        self.assertEqual(related.count("1 shared"), 2)
        self.assertEqual(links(re.search(r"<nav class=\"pager\".*?</nav>", p, re.S).group(0)), ["/questions/calls"])

    def test_an_entry_page_gives_its_kind_text_sources_and_questions(self):
        p = self.pages["/entries/PF1"]
        self.assertIn("<p class=\"eyebrow\">Pitfall <span class=\"cid\">PF1</span></p><h1>Say what the n is</h1>", p)
        content = part(p, "content")
        self.assertIn("Mice are the replicates; lanes are not.", content)
        self.assertRegex(content, r"<h2 id=\"[^\"]+\">Sources</h2>\s*<ul>\s*<li>Squair et al., Nat Commun 2021, "
                                  r"doi:10.1038/s41467-021-25960-2.")
        self.assertEqual(content.count("Squair"), 1)  # the source leaves the text for its own part
        self.assertEqual(links(part(p, "after")), ["/questions/units", "/questions/calls"])
        crumbs = re.search(r"<nav class=\"crumbs\".*?</nav>", p, re.S).group(0)
        self.assertEqual(links(crumbs), ["/", "/pitfalls", "/pitfalls"])  # home, the section, the file's part
        move = self.pages["/moves/T1"]
        self.assertRegex(part(move, "content"), r"<h2 id=\"[^\"]+\">Published screens</h2>\s*<ul>\s*<li>Doe 2020 "
                                                r"\(Fig. 1a\): the guides clustered by their effects")
        self.assertIn("<code>C.genes</code></a>, worth considering when a target has two guides", part(move, "after"))
        self.assertIn("/questions/genes", links(part(move, "after")))
        module = self.pages["/modules/C.genes"]
        self.assertIn("<h1>The genes each target changes</h1>", module)
        self.assertEqual(links(part(module, "after")), ["/questions/units", "/questions/genes"])

    def test_the_moves_page_has_its_kinds_as_tabs_and_the_modules_that_call_for_moves(self):
        p = self.pages["/moves"]
        self.assertEqual(re.findall(r"role=\"tab\"[^>]*data-kind=\"([^\"]*)\">([^<]+)<", p),
                         [("", "All"), ("trust-the-result", "Trust the result"),
                          ("organise-the-effects", "Organise the effects")])
        self.assertEqual(re.findall(r"class=\"chip-btn\"[^>]*data-module=\"([^\"]+)\"", p), ["C.genes"])
        self.assertEqual(re.findall(r"data-kind=\"([^\"]+)\" data-move=\"([^\"]+)\"", p),
                         [("trust-the-result", "T1"), ("organise-the-effects", "O1")])
        self.assertIn("When a target has two or more guides.", p)
        self.assertIn("One way through a lead</button>", p)  # the file's other parts open below the moves
        self.assertEqual(data(self.html)["calls"], {"C.genes": {
            "T1": "worth considering when a target has two guides",
            "O1": "worth considering when 3 or more targets change genes"}})

    def test_the_sidebar_lists_the_steps_with_their_questions_and_the_sections(self):
        nav = re.search(r"<nav class=\"nav\".*?</nav>", self.html, re.S).group(0)
        self.assertEqual(re.findall(r"class=\"fold grp\" data-step=\"([^\"]+)\"", nav), ["design", "effects"])
        self.assertEqual(links(nav), ["/", "/questions", "/questions/step/design", "/questions/units",
                                      "/questions/calls", "/questions/step/effects", "/questions/genes", "/moves",
                                      "/pitfalls", "/modules"])

    def test_the_home_page_counts_each_steps_questions_and_each_sections_entries(self):
        home = self.pages["/"]
        self.assertIn("<h1>Knowledge base</h1>", home)
        metas = re.findall(r"href=\"#([^\"]+)\".*?<span class=\"card-meta\">([^<]+)</span>", home)
        self.assertEqual(metas, [("/questions/step/design", "2 questions"), ("/questions/step/effects", "1 question"),
                                 ("/moves", "2 moves"), ("/pitfalls", "1 pitfall"), ("/modules", "1 module")])

    def test_old_links_and_bare_ids_land_on_their_pages(self):
        d = data(self.html)
        self.assertEqual(d["legacy"], {"q": "/questions/{}", "move": "/moves/{}", "module": "/moves?module={}"})
        self.assertEqual(d["legacy_order"], ["q", "move", "module"])
        # an entry's text links ids as #PF1, which the routes map to the entry's page
        self.assertIn("href=\"#PF1\"", self.pages["/modules/C.genes"])
        self.assertEqual(d["routes"]["PF1"], "/entries/PF1")

    def test_the_page_stands_alone_in_both_themes_and_names_its_base(self):
        ids = re.findall(r"\sid=\"([^\"]+)\"", self.html)
        self.assertEqual(len(ids), len(set(ids)), [i for i in ids if ids.count(i) > 1])
        self.assertIsNone(re.search(r"<(?:script|img|iframe)[^>]*\ssrc=|<link\b|@import|url\(", self.html))
        self.assertIn("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">", self.html)
        self.assertIn("@media (prefers-color-scheme: dark) {\n  :root:not([data-theme=\"light\"]) {", self.html)
        self.assertIn(":root[data-theme=\"dark\"] {", self.html)
        self.assertIn("@media (prefers-reduced-motion: reduce) {", self.html)
        self.assertEqual(kbx.HASH_META.search(self.html).group(1), kbx.base_hash(self.base))
        self.assertIn("at commit <code>abc1234</code>", self.html)

    def test_the_base_hash_follows_the_files_and_not_their_line_endings(self):
        before = kbx.base_hash(self.base)
        path = os.path.join(self.base, "pitfalls.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        try:
            with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
                fh.write(text)
            self.assertEqual(kbx.base_hash(self.base), before)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\nOne more line.\n")
            self.assertNotEqual(kbx.base_hash(self.base), before)
        finally:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

    def test_the_page_is_written_whole_into_its_folder(self):
        out = os.path.join(self.tmp.name, "site")
        path = kbx.write(out, self.base, commit="abc1234", built="2026-10-29")
        self.assertEqual(os.listdir(out), ["index.html"])
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r\n", raw)
        self.assertEqual(raw.decode("utf-8"), self.html)
        self.assertEqual(kbx.hash_in(path), kbx.base_hash(self.base))


class TestThePapers(unittest.TestCase):
    """A paper store (knowledge.db) gives the Papers section: a card per paper, and a page per paper with each result
    as a step, the steps that raised it, its quote, its checks and its evidence; a base without one has no section."""

    def test_a_store_gives_a_page_for_each_paper(self):
        from test_knowledge_store import add, item, store
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "pertura-knowledge")
            write_base(base)
            self.assertNotIn("/papers", pages(kbx.render(base, commit="", built="2026-11-12")))
            db = store.connect(os.path.join(tmp, store.STORE))
            with db:
                add(db, "paper a", "study a", ("CRISPRa", "transcriptome"), [
                    (1, "which pairs interact", [], [], [item(analysis=["combinations"], panels="Fig. 4d")]),
                    (2, "whether equal scores share a mechanism", [1], ["guides agree"], [item(panels="Fig. 5a")])])
            db.close()
            html_text = kbx.render(base, commit="", built="2026-11-12", papers=os.path.join(tmp, store.STORE))
        got = pages(html_text)
        self.assertIn("2 results", got["/papers"])
        paper = got["/papers/paper-a"]
        self.assertIn("Step 2. whether equal scores share a mechanism", paper)
        self.assertIn("Raised by: step 1 · Checks reported: guides agree", paper)  # a line under its result
        self.assertIn("<blockquote>quote: whether equal scores share a mechanism</blockquote>", paper)
        self.assertIn("<tr><td>Fig. 4d</td><td>a target&#x27;s own gene</td>", paper)  # its step's table
        self.assertIn("<b>Method:</b> a mixture model of guide counts", paper)  # the rest, folded
        self.assertIn("https://doi.org/10.1000/paper-a", paper)
        self.assertIn("data-route=\"/papers\" href=\"#/papers\">Papers</a>", html_text)  # the sidebar


class TestThePluginsBase(unittest.TestCase):
    def test_every_entry_question_and_move_has_its_page_with_its_questions(self):
        base = knowledge.load()
        html_text = kbx.render(commit="", built="2026-10-29")
        held = pages(html_text)
        routes = data(html_text)["routes"]
        self.assertEqual({eid for eid in base.entries if routes.get(eid) not in held}, set())
        self.assertEqual([r for r in set(links(html_text)) if r.split("?")[0] not in held], [])
        for qid, q in base.questions().items():
            want = [routes[r] for r in q["read"] if r in routes]
            self.assertEqual(links(part(held[routes[qid]], "after")), want, qid)
        for eid, asked in base.gathered.items():
            if eid in routes and base.get(eid)["kind"] != "question":
                after = links(part(held[routes[eid]], "after"))
                self.assertEqual([r for r in after if r.startswith("/questions/")], [routes[q] for q in asked], eid)
        moves = [eid for eid, x in base.entries.items() if x["kind"] == "follow-up move"]
        self.assertEqual(re.findall(r"data-move=\"([^\"]+)\"", held["/moves"]), moves)

    @unittest.skipUnless(os.path.exists(COMMITTED), "no committed page here")
    def test_the_committed_page_was_built_from_this_base(self):
        self.assertEqual(kbx.hash_in(COMMITTED), kbx.base_hash(knowledge.folder()),
                         "knowledge_base_explorer/index.html was built from another knowledge base; rebuild it with: "
                         f"{kbx.REBUILD}")


if __name__ == "__main__":
    unittest.main()
