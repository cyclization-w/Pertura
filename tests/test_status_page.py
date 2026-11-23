"""The user's page of findings and figures (docs/adr/0032, D14; build step 3, bar 2): the hook saves the figures a
Pertura tool call returned to the agent, and writes a local page of the findings, each with its figure and the
user's replies, and every figure, latest first."""
import base64
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
if not os.path.isdir(STATUS):  # the plugin is not deployed to the analysis server
    raise unittest.SkipTest("no plugin folder here")
sys.path.insert(0, STATUS)
import page  # noqa: E402
import status_file as sf  # noqa: E402

PREFIX = "mcp__plugin_pertura_pertura__"
KEY = sf.project_key("/ws/screen_X-1a2b3c4d")  # the project's status file and page folder
# a 1x1 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def call(tool, tool_input, reply, images=0, session="s1"):
    """A PostToolUse hook's input for a Pertura tool call whose response holds the reply's text and images."""
    content = [{"type": "text", "text": json.dumps(reply)}]
    content += [{"type": "image", "data": base64.b64encode(PNG).decode("ascii"), "mimeType": "image/png"}
                for _ in range(images)]
    return {"session_id": session, "tool_name": PREFIX + tool, "tool_input": tool_input, "tool_response": content,
            "cwd": "."}


def view(text, vid):
    """One view of the page, by its id: the section from its start to its end."""
    part = text[text.index(f'<section class="view" id="{vid}"'):]
    return part[:part.index("</section>")]


def as_claude_code_gives_it(hook_input):
    """The same call as Claude Code gives it to a hook: the tool's output as Claude Code keeps it, which its
    transcript records too. The structured content stands beside the blocks, and each image is under "source"."""
    blocks = hook_input["tool_response"]
    images = [{"type": "image", "source": {"type": "base64", "media_type": b["mimeType"], "data": b["data"]}}
              for b in blocks if b["type"] == "image"]
    texts = [b for b in blocks if b["type"] == "text"]
    return {**hook_input, "tool_response": {"content": images + texts,
                                            "structuredContent": json.loads(texts[0]["text"])}}


class TestThePage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def folder(self):
        return os.path.join(self.root, "projects", KEY)

    def test_the_images_of_a_response_are_found_in_any_shape(self):
        blocks = call("run_code", {}, {"run_id": "r1"}, images=2)["tool_response"]
        self.assertEqual(len(page.response_images(blocks)), 2)
        self.assertEqual(len(page.response_images({"content": blocks})), 2)
        self.assertEqual(page.response_images([{"type": "text", "text": "{}"}]), [])

    def test_a_cells_figures_and_a_finding_with_its_reply_reach_the_page(self):
        sf.record_call(call("open_project", {"data_folder": "/data/X"}, {"project": "/ws/screen_X-1a2b3c4d"}),
                       self.root)
        sf.record_call(call("run_code", {"what": "cells per capture", "code": "..."},
                            {"run_id": "run0001-aa", "status": "ok",
                             "figures_shown": ["/ws/screen_X-1a2b3c4d/notebooks/main/figures/cell001_1.png"]},
                            images=1), self.root)
        sf.record_call(call("record_finding", {"finding_id": "f1", "run_ids": ["run0001-aa"],
                                               "statement": "The captures hold as many cells each.",
                                               "figure": "/ws/screen_X-1a2b3c4d/notebooks/main/figures/cell001_1.png"},
                            {"finding_id": "f1", "checks": "none"}), self.root)
        sf.record_call(call("note_reply", {"finding_id": "f1", "kind": "keep", "words": "Keep it."},
                            {"recorded": "keep"}), self.root)
        folder = self.folder()
        figures = os.listdir(os.path.join(folder, page.FIGURES))
        self.assertEqual(len(figures), 1)
        with open(os.path.join(folder, page.PAGE_NAME), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("The captures hold as many cells each.", text)
        self.assertIn(f"{page.FIGURES}/{figures[0]}", text)
        self.assertIn("Keep it.", text)
        self.assertIn("cells per capture", text)
        self.assertEqual(text.count(f'src="{page.FIGURES}/{figures[0]}"'), 2)  # with its finding, and with its result

    def test_a_figure_in_the_form_claude_code_gives_its_hooks_reaches_the_page(self):
        sf.record_call(as_claude_code_gives_it(call("open_project", {"data_folder": "/data/X"},
                                                    {"project": "/ws/screen_X-1a2b3c4d"})), self.root)
        hook_input = as_claude_code_gives_it(call("run_code", {"what": "a figure", "code": "..."},
                                                  {"run_id": "run0001-aa", "status": "ok"}, images=1))
        self.assertEqual(page.response_images(hook_input["tool_response"]), [(PNG, "image/png")])
        sf.record_call(hook_input, self.root)
        folder = self.folder()
        figures = os.listdir(os.path.join(folder, page.FIGURES))
        self.assertEqual(len(figures), 1)
        with open(os.path.join(folder, page.FIGURES, figures[0]), "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        with open(os.path.join(folder, page.PAGE_NAME), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"{page.FIGURES}/{figures[0]}", text)
        self.assertIn("a figure", text)

    def test_a_module_result_and_a_branch_add_their_figures_and_errors_add_nothing(self):
        sf.record_call(call("open_project", {}, {"project": "/ws/screen_X-1a2b3c4d"}), self.root)
        sf.record_call(call("module_result", {"run_id": "m1"}, {"run_id": "m1", "state": "done",
                                                              "figure": "/jobs/m1/figure.png", "summary": "4 guides"},
                            images=1), self.root)
        sf.record_call(call("close_branch", {"branch_id": "hvg"}, {"branch_id": "hvg",
                                                                 "figures_shown": ["/a.png", "/b.png"]}, images=2),
                       self.root)
        sf.record_call(call("record_finding", {"finding_id": "bad"}, {"error": "a finding says what was found"}),
                       self.root)
        st = sf.read_json(sf.project_path(KEY, self.root))
        self.assertEqual(len(st["page"]["figures"]), 3)
        self.assertEqual(st["page"]["figures"][0]["server"], "/b.png")  # latest first
        self.assertEqual(st["page"]["findings"], {})


class TestThePageAfterSession5(unittest.TestCase):
    """docs/build_goal_session5_fixes.md, step 6: the design card, the results, the findings' figures, the reload,
    the browser opened once, and the jobs the status line watches."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.opened = []
        self.call("open_project", {"data_folder": "/data/X"}, {"project": "/ws/screen_X-1a2b3c4d"})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool, args, reply, images=0, session="s1"):
        return sf.record_call(call(tool, args, reply, images, session), self.root, open_page=self.opened.append)

    def text(self):
        with open(os.path.join(self.root, "projects", KEY, page.PAGE_NAME), encoding="utf-8") as fh:
            return fh.read()

    def test_a_notebook_result_without_a_figure_is_in_the_results(self):
        self.call("run_code", {"what": "cells per capture, counted", "code": "..."},
                  {"run_id": "run0002-bb", "status": "ok", "stdout": "D1 2753\nD2 3057\n"})
        self.call("run_code", {"what": "a cell that fails", "code": "..."},
                  {"run_id": "run0003-cc", "status": "error", "error": "KeyError: 'sample'"})
        self.call("run_module", {"module": "B.guide_calls_and_their_evidence"}, {"run_id": "m2", "state": "running"})
        self.call("module_result", {"run_id": "m2"}, {"run_id": "m2", "state": "done", "job_id": "m2",
                                                    "summary": "20,717 cells with one target"})
        text = self.text()
        self.assertIn("cells per capture, counted", text)
        self.assertIn("D1 2753; D2 3057", text)  # its result in a line
        self.assertIn("<b>Guide calls and their evidence</b>", text)  # an analysis in words, not its code
        self.assertNotIn("B.guide_calls", text)
        self.assertIn("error: KeyError: &#x27;sample&#x27;", text)
        self.assertLess(text.index("cells per capture, counted"), text.index("a cell that fails"))  # newest last

    def test_the_design_card_shows_each_item_with_its_origin(self):
        self.call("confirm_design", {}, {"design_confirmed": True,
                                         "confirmed_values": {"Modality": "several modes, each guide's own",
                                                              "Batches": "not recorded"},
                                         "origins": {"Modality": "stated in the files",
                                                     "Batches": "Pertura's default"}})
        text = self.text()
        design = view(text, "design")
        self.assertIn("several modes, each guide&#x27;s own", design)
        self.assertEqual(design.count("You confirmed it at"), 1)  # once, not on each item
        self.assertNotIn("Confirmed by you", design)
        self.assertIn("<summary>Source</summary>stated in the files</details>", design)  # where it came from, folded
        self.assertIn("<summary>Source</summary>Pertura&#x27;s default</details>", design)

    def test_a_findings_card_shows_its_runs_figure_and_its_earlier_wording(self):
        self.call("run_code", {"what": "HBZ in GATA1's cells", "code": "..."}, {"run_id": "run0004-dd", "status": "ok"},
                  images=1)
        self.call("record_finding", {"finding_id": "f2", "run_ids": ["run0004-dd"],
                                     "statement": "GATA1 repression lowers HBZ."},
                  {"finding_id": "f2", "checks": "none", "figure": "none named"})
        self.call("reword_finding", {"finding_id": "f2", "reason": "the checks' number",
                                     "statement": "GATA1 repression lowers HBZ (log2 FC -1.2)."},
                  {"finding_id": "f2", "checks": "kept"})
        name = os.listdir(os.path.join(self.root, "projects", KEY, page.FIGURES))[0]
        text = self.text()
        card = view(text, "finding-f2")
        self.assertIn(f"{page.FIGURES}/{name}", card)
        self.assertIn("GATA1 repression lowers HBZ (log2 FC -1.2).", card)
        self.assertIn("Revision history", card)
        self.assertIn("reason: the checks&#x27; number", card)

    def test_the_plan_reaches_the_page_with_each_items_state(self):
        pg = page.fold(None, "page_state", {}, {"plan": {"version": 2, "why": "the map shows one target strongest",
                                                         "items": [
            {"id": 1, "does": "genes changed per target", "how": "run_module C.genes", "status": "done", "runs": ["r1"]},
            {"id": 2, "does": "the target's programme", "how": "the notebook", "status": "open", "runs": []},
            {"id": 3, "does": "the small groups' state", "how": "run_module C.within_states", "status": "left",
             "left_why": "it tracks the groups' size"}], "next": "your plan's next item 2"}}, [], 0)
        text = page.render(pg, "x")
        plan = view(text, "plan")
        for said in ("version 2; the map shows one target strongest", "<b>Next:</b> your plan&#x27;s next item 2",
                     "genes changed per target", "1 run",
                     "the target&#x27;s programme", "it tracks the groups&#x27; size"):
            self.assertIn(said, plan)
        self.assertIn("No plan yet", page.render(page.fold(None, "read_record", {}, {}, [], 0), "x"))

    def test_a_findings_figure_is_of_the_first_of_its_runs_that_drew_one(self):
        pg = page.empty()
        pg["figures"] = [{"file": "cell-s08.png", "run_id": "cell", "server": "/w/figures/s08.png"},  # latest first
                         {"file": "cell-hdac1.png", "run_id": "cell", "server": "/w/figures/hdac1.png"},
                         {"file": "states.png", "run_id": "states", "server": "/w/jobs/states/figure.png"}]
        finding = {"run_ids": ["states", "cell"]}  # the run it rests on, then a cell that drew two findings' figures
        self.assertEqual(page._figure_of(pg, finding)["file"], "states.png")
        self.assertEqual(page._figure_of(pg, {**finding, "figure": "/w/figures/hdac1.png"})["file"], "cell-hdac1.png")
        self.assertIsNone(page._figure_of(pg, {"run_ids": ["another"]}))

    def test_the_page_reloads_itself_and_a_click_enlarges_a_figure(self):
        text = self.text()
        self.assertIn("location.reload()", text)
        self.assertIn(str(page.RELOAD_SECONDS * 1000), text)
        self.assertIn("id=\"lightbox\"", text)

    def test_the_browser_opens_the_page_once_per_session_and_project(self):
        self.call("pertura_status", {}, {"project": "/ws/screen_X-1a2b3c4d"})
        self.call("run_code", {"what": "a cell"}, {"run_id": "run0005-ee", "status": "ok"})
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(self.opened[0].endswith(page.PAGE_NAME))
        self.call("pertura_status", {}, {"project": "/ws/screen_X-1a2b3c4d"}, session="s2")  # a new session
        self.assertEqual(len(self.opened), 2)
        sf.record_call(call("run_code", {"what": "a cell"}, {"run_id": "run0006-ff", "status": "ok"}, session="s3"),
                       self.root)  # no opener given: nothing opened
        self.assertEqual(len(self.opened), 2)

    def test_the_option_turns_the_opening_off(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {sf.OPEN_PAGE_VARIABLE: "false"}):
            self.assertFalse(sf.open_page_wanted())
        with mock.patch.dict(os.environ, {sf.OPEN_PAGE_VARIABLE: "true"}):
            self.assertTrue(sf.open_page_wanted())

    def test_a_notebook_run_is_never_watched_as_a_job(self):
        st = self.call("module_result", {"run_id": "run0021-d4a22e"},
                       {"run_id": "run0021-d4a22e", "state": "ok", "job_id": None})
        self.assertNotIn("run0021-d4a22e", st["jobs"])
        with open(sf.project_path(KEY, self.root, ".line"), encoding="utf-8") as fh:
            self.assertNotIn("run0021-d4a22e", fh.read())
        st = self.call("module_result", {"run_id": "m1"}, {"run_id": "m1", "state": "done", "job_id": "m1",
                                                         "summary": "4 guides"})
        self.assertEqual(st["jobs"]["m1"]["state"], "done")


class TestTheKnowledgeBesideThePage(unittest.TestCase):
    """docs/build_goal_knowledge_base.md, step 4: the knowledge base as a page beside the user's, and each result and
    finding linked to the entries its reply named."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        sf.record_call(call("open_project", {"data_folder": "/data/X"}, {"project": "/ws/screen_X-1a2b3c4d"}),
                       self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_entry_of_the_index_has_its_anchor_and_no_link_dangles(self):
        import re
        import knowledge_page
        text = knowledge_page.render()
        anchors = set(re.findall(r'id="([^"]+)"', text))
        names = knowledge_page.titles()
        self.assertGreater(len(names), 100)
        self.assertEqual([i for i in names if i not in anchors], [])
        self.assertEqual(sorted(set(re.findall(r'href="#([^"]+)"', text)) - anchors), [])
        for step in ("P1.1", "P6.15"):
            self.assertIn(step, anchors)
        self.assertNotIn("href", knowledge_page.inline("the S and G2/M genes", {"G2"}))  # a phase, not G2

    def test_a_result_and_a_finding_link_to_the_entries_their_replies_named(self):
        import re
        import knowledge_page
        sf.record_call(call("run_module", {"module": "B.efficacy"}, {"run_id": "m1", "state": "running"}), self.root)
        sf.record_call(call("module_result", {"run_id": "m1"}, {
            "run_id": "m1", "state": "done", "job_id": "m1", "summary": "29 guides work",
            "read_beside_it": {"ways": [{"way": "w", "read": ["G3", "PF7"]},
                                        {"way": "x", "read": "no knowledge yet (a card)"}],
                               "also_read": ["PF5"], "how": "h"},
            "moves": [{"move": "each perturbation acts", "id": "T3"}]}), self.root)
        sf.record_call(call("record_finding", {"finding_id": "f1", "statement": "Most guides act."},
                            {"finding_id": "f1", "read_beside_it": {"moments": [{"read": ["PF1", "PF16"]}]}}),
                       self.root)
        folder = os.path.join(self.root, "projects", KEY)
        with open(os.path.join(folder, page.PAGE_NAME), encoding="utf-8") as fh:
            text = fh.read()
        linked = re.findall(r'href="knowledge.html#([^"]+)"', text)
        self.assertEqual(linked, ["PF1", "PF16", "G3", "PF7", "PF5", "T3"])  # the finding first, then the result
        self.assertIn(">A knockout whose target gene does not fall</a>", text)  # in words, its id on hover
        self.assertTrue(os.path.exists(os.path.join(folder, knowledge_page.NAME)))
        with open(os.path.join(folder, knowledge_page.NAME), encoding="utf-8") as fh:
            anchors = set(re.findall(r'id="([^"]+)"', fh.read()))
        self.assertEqual([i for i in linked if i not in anchors], [])

    def test_the_knowledge_page_searches_gives_the_questions_and_what_this_project_read(self):
        """The knowledge page by questions: a search, the questions with their short answers marked as Pertura's
        reading, each entry with its questions, and the entries this project read, each linked back to its card."""
        import re
        import knowledge_page
        sf.record_call(call("run_module", {"module": "B.efficacy"}, {"run_id": "m1", "state": "running"}), self.root)
        sf.record_call(call("module_result", {"run_id": "m1"}, {
            "run_id": "m1", "state": "done", "job_id": "m1", "summary": "29 guides work",
            "read_beside_it": {"ways": [{"way": "w", "read": ["G3", "PF7"]}], "how": "h"}}), self.root)
        sf.record_call(call("record_finding", {"finding_id": "f1", "statement": "Most guides act."},
                            {"finding_id": "f1", "read_beside_it": {"moments": [{"read": ["PF1"]}]}}), self.root)
        folder = os.path.join(self.root, "projects", KEY)
        with open(os.path.join(folder, page.PAGE_NAME), encoding="utf-8") as fh:
            mine = fh.read()
        self.assertIn('id="finding-f1"', mine)
        self.assertIn('id="result-m1"', mine)
        path = os.path.join(folder, knowledge_page.NAME)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('<input id="q" type="search"', text)
        project = text[text.index('<section id="project">'):text.index('<section id="questions">')]
        self.assertEqual(re.findall(r'href="#([A-Z]+\d+)"', project), ["PF1", "G3", "PF7"])  # the finding first
        self.assertIn('href="page.html#finding-f1"', project)
        self.assertIn('href="page.html#result-m1"', project)
        self.assertIn('href="#units"', project)  # PF1's questions
        units = text[text.index('<article class="question" id="units">'):]
        units = units[:units.index("</article>")]
        self.assertIn('<span class="draft">Draft</span>', units)
        self.assertIn('<p class="sources"><b>Sources:</b>', units)  # the papers behind the answer, beside it
        self.assertIn('href="#PF2"', units)
        g3 = text[text.index('<h3 id="G3">'):]
        self.assertIn('Questions it answers: <a href="#efficacy">', g3[:g3.index("</p>") + 4])
        # written again only when the project's entries change
        used = [["PF1", [["finding f1: Most guides act.", "page.html#finding-f1"]]]]
        knowledge_page.write_if_old(folder, used=used)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("<!-- kept -->")
        knowledge_page.write_if_old(folder, used=used)
        with open(path, encoding="utf-8") as fh:
            self.assertTrue(fh.read().endswith("<!-- kept -->"))
        knowledge_page.write_if_old(folder, used=used + [["PF16", [["a result", "page.html#result-m2"]]]])
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("<!-- kept -->", fh.read())

    def read_page(self, name=page.PAGE_NAME):
        with open(os.path.join(self.root, "projects", KEY, name), encoding="utf-8") as fh:
            return fh.read()

    def test_a_read_of_the_base_is_a_step_of_its_own_with_its_entries_and_a_question_marked_draft(self):
        """Replies carry no entries since a reply carries its call's result; what the agent reads on request is a
        step of its own, its entries linked, a question marked Draft as the base marks its short answers."""
        import re
        import knowledge_page
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from pertura import knowledge
        sf.record_call(call("read_knowledge", {"ids": ["units", "U1"]}, knowledge.read_reply(ids=["units", "U1"])),
                       self.root)
        sf.record_call(call("read_knowledge", {"words": "shared response"},
                            knowledge.read_reply(words="shared response")), self.root)
        sf.record_call(call("read_knowledge", {"ids": ["U1"]}, {"error": "no such call"}), self.root)
        text = self.read_page()
        results = view(text, "timeline")
        self.assertEqual(results.count("<b>Read the knowledge base"), 2)  # the failed call adds nothing
        self.assertLess(results.index("<b>Read the knowledge base: ids units, U1</b>"),
                        results.index("<b>Read the knowledge base: words shared response</b>"))  # the newest last
        self.assertIn("read: units, U1", results)
        self.assertIn("found: ", results)
        read = results[results.index("ids units, U1"):]
        read = read[:read.index("<div class=\"ev\"")]
        self.assertEqual(re.findall(r'href="knowledge.html#([^"]+)"', read), ["units", "U1"])
        self.assertIn('<p class="reply">Entries: ', read)
        self.assertIn('<span class="draft">Draft</span> The unit an effect is tested over</a>', read)  # a question
        self.assertIn(">Replicates behind the cells</a>", read)  # a card, not marked
        self.assertNotIn("Draft</span> Replicates", read)
        words = results[results.index("words shared response"):]
        self.assertNotIn('href="knowledge.html#', words)  # found by words: named, not read
        project = self.read_page(knowledge_page.NAME)
        project = project[project.index('<section id="project">'):project.index('<section id="questions">')]
        self.assertIn('href="#U1"', project)
        self.assertIn('href="page.html#result-', project)  # linked back to the step that read it

    def test_the_prior_knowledge_and_the_differences_given_with_a_finding_a_thread_or_a_question_are_beside_them(self):
        sf.record_call(call("open_thread", {"about": "Why IRF1 is up", "prior": {"text": "IRF1 follows IFN-gamma.",
                                                                                 "source": "PMID 123"}},
                            {"thread_id": "t1"}), self.root)
        sf.record_call(call("record_finding", {
            "finding_id": "f1", "statement": "IRF1 rises.", "thread_id": "t1",
            "prior": {"text": "IRF1 is an interferon gene.", "source": "PMID 456", "citation": "Smith 2020"},
            "differs": {"text": "The paper saw it fall.", "source": "the paper"}}, {"finding_id": "f1"}), self.root)
        sf.record_call(call("note_difference", {"finding_id": "f1", "source": "prior knowledge",
                                                "text": "Larger than reported."}, {"finding_id": "f1"}), self.root)
        sf.record_call(call("reword_finding", {"finding_id": "f1", "reason": "r", "prior": {
            "text": "A second source agrees.", "source": "the user"}}, {"finding_id": "f1"}), self.root)
        sf.record_call(call("record_knowledge", {"question_id": "Q2", "source": "PubMed", "citation": "PMID 789",
                                                 "text": "STAT1 drives IRF1."}, {"recorded": True}), self.root)
        sf.record_call(call("note_difference", {"finding_id": "f1", "source": "the paper", "text": "Not kept."},
                            {"error": "no finding f1"}), self.root)
        text = self.read_page()
        finding = view(text, "finding-f1")
        self.assertIn("<b>Prior knowledge</b>: IRF1 is an interferon gene. <span class=\"muted\">(PMID 456, Smith 2020)"
                      "</span>", finding)
        self.assertIn("<b>Prior knowledge</b>: A second source agrees. <span class=\"muted\">(the user)</span>",
                      finding)
        self.assertIn("<b>Differs from the paper</b>: The paper saw it fall.", finding)
        self.assertIn("<b>Differs from prior knowledge</b>: Larger than reported.", finding)
        self.assertNotIn("Not kept", text)  # a failed call adds nothing
        self.assertNotIn("IRF1 follows IFN-gamma", finding)
        thread = text[text.index('id="thread-t1"'):]
        thread = thread[:thread.index("</article>")]
        self.assertIn("<b>Prior knowledge</b>: IRF1 follows IFN-gamma. <span class=\"muted\">(PMID 123)</span>", thread)
        self.assertNotIn("IRF1 is an interferon gene", thread)
        self.assertIn("<h2>Prior knowledge for plan questions</h2>", text)
        self.assertIn("Q2: <b>Prior knowledge</b>: STAT1 drives IRF1. <span class=\"muted\">(PubMed, PMID 789)",
                      text)


if __name__ == "__main__":
    unittest.main()


class TestThePageAsAPaper(unittest.TestCase):
    """The 11-06 design in the page: an overview, a page for each result and finding, the methods written from the
    runs, the timeline, the sidebar and the search; it reads the page's state, the record as the server gives it and
    the runs' methods."""

    def render(self, text="Ifnar1 is lower [finding:f1, run:run0001-aa]."):
        pg = page.fold(None, "run_code", {"what": "the interferon score per target", "code": "..."},
                       {"run_id": "run0001-aa", "status": "ok", "stdout": "Ifnar1 -0.53",
                        "figures_shown": ["/ws/p/figures/score.png"]}, ["score-1.png"], now=100.0)
        pg = page.fold(pg, "record_finding", {"finding_id": "f1", "statement": "Ifnar1 lowers the score.",
                                              "run_ids": ["run0001-aa"], "figure": "/ws/p/figures/score.png"},
                       {"finding_id": "f1"}, [], now=200.0)
        record = {"findings": {"f1": {"statement": "Ifnar1 lowers the score.", "run_ids": ["run0001-aa"],
                                      "png": "/ws/p/figures/score.png", "kind": "gene_set",
                                      "checks": {"Ifnar1": {"lead": {"outcome": "agrees", "test": "linear_model",
                                                                     "cells": 37, "q": 1.1763e-38},
                                                            "half": {"outcome": "not run", "reason": "0 units"}}}}},
                  "results": {"r1": {"title": "The interferon axis", "finding_ids": ["f1"], "decision": "confirmed",
                                     "png": "/ws/p/figures/score.png", "text": text,
                                     "time": "2026-11-08T14:13:00"}},
                  "threads": {}, "module_menus": [],
                  "lead": {"verdict": "complete", "story": "The main line is the interferon axis.", "time": None}}
        methods = [{"run_id": "20261108T130136-C-genes-d09815", "module": "C.genes", "why": "genes per target",
                    "summary": "122 of 135 targets change genes",
                    "packages": [{"package": "edgeR", "version": "4.8.2", "function": "glmQLFit"}],
                    "facts": {"multiple_testing": "tests 3632; corrected together per target"}}]
        return page.render(pg, "Pertura · screen_X", record, methods)

    def test_the_overview_leads_with_the_results_and_the_leads_account(self):
        text = self.render()
        overview = view(text, "overview")
        self.assertIn("<h1>screen_X</h1>", overview)
        self.assertIn("The main line is the interferon axis.", overview)
        self.assertIn('href="#report-r1">The interferon axis</a>', overview)
        self.assertIn(f"{page.FIGURES}/score-1.png", overview)  # the result's figure, from the server's path
        self.assertIn('<a href="#finding-f1" title="Finding f1">', overview)  # its id on hover
        self.assertIn('<p class="first">Ifnar1 is lower.</p>', overview)  # the card's preview leaves the marks out

    def test_a_result_and_a_finding_each_have_a_page_figure_first(self):
        text = self.render()
        report = view(text, "report-r1")
        self.assertLess(report.index(f"{page.FIGURES}/score-1.png"), report.index("Ifnar1 is lower"))
        # a mark naming two, each linked: the finding by its number in the list below, as papers cite; the run by its id
        self.assertIn('lower<sup class="refs"><a href="#finding-f1" title="Finding f1">1</a></sup>', report)
        self.assertIn('<a class="ref" href="#result-run0001-aa">run0001-aa</a>', report)
        self.assertIn('<span class="no">1</span>', report)
        finding = view(text, "finding-f1")
        self.assertLess(finding.index(f"{page.FIGURES}/score-1.png"), finding.index("<h2>Checks</h2>"))
        self.assertIn("test linear_model; cells 37; q 1.18e-38", finding)  # numbers as a reader reads them
        self.assertIn("0 units", finding)
        self.assertIn('href="#report-r1">Result 1</a>', finding)

    def test_a_results_labelled_parts_each_start_a_paragraph(self):
        report = view(self.render("Observation: Ifnar1 is lower [finding:f1]. Inference: the axis acts. "
                                  "Independent evidence: none yet."), "report-r1")
        self.assertIn('<p class="prose"><b>Observation:</b> Ifnar1 is lower<sup class="refs"><a href="#finding-f1" '
                      'title="Finding f1">1</a></sup>.</p><p class="prose"><b>Inference:</b> the axis acts.</p>'
                      '<p class="prose"><b>Independent evidence:</b> none yet.</p>', report)

    def test_results_keep_the_order_first_proposed_when_one_is_revised(self):
        record = {"findings": {}, "threads": {}, "module_menus": [], "lead": {},
                  "results": {"r1": {"title": "First proposed", "finding_ids": [], "version": 2, "text": "a.",
                                     "time": "2026-11-08T15:00:00"},
                              "r2": {"title": "Then this one", "finding_ids": [], "version": 1, "text": "b.",
                                     "time": "2026-11-08T14:00:00"}}}
        text = page.render(page.empty(), "Pertura · screen_X", record, [])
        overview = view(text, "overview")
        self.assertLess(overview.index("First proposed"), overview.index("Then this one"))
        self.assertIn("Result 1", view(text, "report-r1"))

    def test_the_methods_are_written_from_the_runs_and_the_timeline_holds_the_steps_in_order(self):
        text = self.render()
        methods = view(text, "methods")
        self.assertIn("edgeR 4.8.2 (glmQLFit)", methods)
        self.assertIn("multiple testing: tests 3632; corrected together per target", methods)
        self.assertIn("<span class=\"run\">13:01</span> genes per target", methods)
        self.assertIn('id="result-run0001-aa"', view(text, "timeline"))
        side = text[text.index('<aside class="side">'):text.index("</aside>")]
        for vid in ("overview", "report-r1", "methods", "findings", "threads", "design", "timeline"):
            self.assertIn(f'data-view="{vid}"', side)
        self.assertNotIn('data-view="lead"', side)  # the lead's and the plan's parts show once they hold anything
        self.assertIn('class="alien', side)
        data = json.loads(text[text.index('id="search-data">') + 17:text.index("</script>", text.index('id="search-data">'))])
        self.assertIn({"g": "Findings", "t": "Ifnar1 lowers the score.", "s": "f1", "h": "#finding-f1"}, data)


class TestThePageReadsLikeAPaper(unittest.TestCase):
    """A finding titled by its statement's first clause; a thread's steps folded under their count, each by what it did
    and why, a failure in sight; nothing marked new on the first visit."""

    def test_a_findings_title_is_its_statements_first_sentence_cut_at_a_clause(self):
        self.assertEqual(page._title("Set A carries no shared NF-kB signature in these cells. Only CD27 raises RELA."),
                         "Set A carries no shared NF-kB signature in these cells")
        self.assertEqual(page._title("Set B cells lower TNFRSF9 relative to control cells (GRAP -1.31, MAP4K1 -1.25, "
                                     "MUC1 -1.22, LAT2 -0.91, SLA2 -0.62), the opposite of set A on TNFRSF9."),
                         "Set B cells lower TNFRSF9 relative to control cells")  # not inside the parenthesis
        self.assertEqual(page._title("Set B (GRAP, LAT2) lowers the activation output, and the condition-split run "
                                     "shows that set B acts mostly when the cells are restimulated."),
                         "Set B (GRAP, LAT2) lowers the activation output")
        long = "word " * 40
        self.assertTrue(page._title(long).endswith("…") and len(page._title(long)) <= page.TITLE_AT + 1)

    def test_a_threads_steps_fold_under_their_count_with_what_and_why(self):
        pg = page.empty()
        pg["threads"] = {"t1": {"thread_id": "t1", "about": "Stat1's cells differ", "status": "open", "time": 1.0,
                                "last": 3.0}}
        pg["results"] = [  # the page keeps the latest first
            {"time": 3.0, "what": "load the scores", "result": "error: PermissionError: no", "thread_id": "t1",
             "figures": [], "tool": "run_code", "run_id": "r2"},
            {"time": 2.0, "what": "inspect columns", "why": "to find the depth column", "thread_id": "t1",
             "result": "['sample', 'unit', 'umis']", "figures": [], "tool": "run_code", "run_id": "r1"}]
        threads = view(page.render(pg, "Pertura · screen_X", {}, []), "threads")
        self.assertIn('<details class="steps" data-k="steps-t1"><summary>2 steps</summary>', threads)
        self.assertIn('inspect columns</a> <span class="muted">to find the depth column</span>', threads)
        self.assertIn('<span class="failed">error: PermissionError: no</span>', threads)  # a failure stays in sight
        self.assertNotIn("&#x27;umis&#x27;", threads)  # a step's raw output is in the step itself

    def test_the_first_visit_marks_nothing_new(self):
        self.assertIn("if(!s){s=Date.now()/1000;localStorage.setItem(k,s)}", page.NEW_SCRIPT)


class TestTheSectionsNeitherPageDrew(unittest.TestCase):
    """The first look's sections, the screen at a glance, the module menus, how the deposit is read (the samples not
    analysed and the files not read among it) and the build's reading checks."""

    def render(self):
        pg = page.fold(None, "describe_deposit", {}, {
            "samples": [{"name": "A1", "kind": "h5", "matrix": "A1.h5"}],
            "read_into_cell_tables": {"proposed_by": "pertura:discover", "samples": {
                "A1": {"read_by": "pertura:discover", "cells": 7000, "from": ["A1.h5"]}},
                "notes": ["A2: not read (a matrix of every droplet), by run0003-cc", "A1: 12 features read as guides"]},
            "unreadable": [{"file": "B.loom", "error": "not a loom file"}]}, [], now=10.0)
        pg = page.fold(pg, "build_screen_object", {}, {"reading_checks": {
            "facts": ["Cells without a unit: 22,615 (A1, 3,544)"], "do": "say for each"}}, [], now=20.0)
        pg = page.fold(pg, "module_result", {"run_id": "m1"}, {"run_id": "m1", "state": "done", "summary": "map",
                                                               "screen_overview": {"columns": {"cells": {"state": "filled"},
                                                                                               "e_distance": {"module": "C.any_effect"}},
                                                                                   "rows": [{"target": "Stat1", "cells": 37, "e_distance": 0.2241}]}},
                       [], now=30.0)
        record = {"findings": {}, "results": {}, "threads": {},
                  "first_look": [{"section": "states_map", "label": "The cells' states", "run_id": "m1",
                                  "png": None, "finding_ids": []}],
                  "module_menus": [{"time": "2026-11-08T13:08:48", "could_run": ["C.genes", "F.guides_agree"],
                                    "picks": [{"module": "F.guides_agree", "why": "f1 rests on four targets"}],
                                    "chosen": ["F.guides_agree"], "action": "unattended", "note": None}]}
        return page.render(pg, "Pertura · screen_X", record, [])

    def test_the_overview_holds_the_first_look_and_the_screen_at_a_glance(self):
        overview = view(self.render(), "overview")
        self.assertIn("The cells&#x27; states", overview)
        self.assertIn("No findings yet", overview)
        self.assertIn("0 of 1 sections with findings", overview)
        self.assertIn('<th title="from the screen object">cells</th>', overview)  # where each column came from
        self.assertIn("<th>Stat1</th><td>37</td><td>0.224</td>", overview)

    def test_the_design_says_how_the_deposit_is_read_and_what_the_build_read(self):
        design = view(self.render(), "design")
        self.assertIn("<th>A1</th><td>pertura:discover</td><td>7000</td>", design)
        self.assertIn("<b>A2</b>: a matrix of every droplet, by run0003-cc", design)  # a sample not analysed
        self.assertIn("A1: 12 features read as guides", design)  # the other notes
        self.assertIn("<code>B.loom</code>: not a loom file", design)
        self.assertIn("Cells without a unit: 22,615 (A1, 3,544)", design)

    def test_the_plan_holds_the_module_menus_and_what_ran(self):
        plan = view(self.render(), "plan")
        self.assertIn("Selected automatically", plan)
        self.assertIn("f1 rests on four targets", plan)
        self.assertIn('<i class="sq good"', plan)


class TestWhatWaitsAndTheRecordsWordsOnThePage(unittest.TestCase):
    """What waits on the user comes from the record, each with its place; a check not run counts neither way; a
    first-look section shows its own figure or none; the record's states read as an interface says them."""

    def page_with(self, **fields):
        pg = page.empty()
        pg.update(fields)
        return pg

    def test_nothing_waits_on_the_user_when_the_record_holds_nothing_to_settle(self):
        pg = page.fold(None, "pertura_status", {}, {"digest": {
            "line": "on t1 · waits on you: you said enough", "waits_on_you": ["you said enough: the report comes next"],
            "on": None, "since": [], "next": []}}, [], now=10.0)
        pg = page.fold(pg, "confirm_design", {}, {"design_confirmed": True, "confirmed_values": {"Organism": "mouse"},
                                                  "origins": {}}, [], now=20.0)
        record = {"results": {"r1": {"title": "Ifnar1 lowers the response", "decision": "confirmed",
                                     "finding_ids": []}}, "story": "The story.", "complete": True}
        text = page.render(pg, "Pertura · screen_X", record, [])
        overview, side = view(text, "overview"), text[text.index('<aside class="side">'):text.index("</aside>")]
        self.assertNotIn("Needs your input", overview + side)  # (the script holds the words, for a form)
        self.assertIn('<span class="l1">Complete, step', text)  # the pill, on every view (the sidebar names the screen)
        self.assertNotIn("you said enough", text)  # the server's own sentences stay the terminal's
        self.assertIn("Result 1, in the report", overview)
        self.assertLess(overview.index("<b>Summary</b>"), overview.index('class="tabs"'))  # the summary, then the parts
        self.assertIn('<li><a href="#report-r1">Ifnar1 lowers the response</a></li>', overview)  # results' titles in it
        self.assertIn('<div class="tab-panel on" role="tabpanel" data-panel="results">', overview)  # results first

    def test_the_overview_shows_the_summarys_start_and_before_any_result_the_status_first(self):
        pg = page.fold(None, "pertura_status", {}, {"digest": {
            "line": "on t1", "on": {"thread_id": "t1", "about": "Stat1's cells differ", "why": "the strongest"},
            "since": [], "next": []}}, [], now=10.0)
        record = {"lead": {"verdict": None, "story": "Stat1 lowers the score. Its guides agree. Irf1 is open."}}
        overview = view(page.render(pg, "Pertura · screen_X", record, []), "overview")
        self.assertIn('<p class="lead-text">Stat1 lowers the score. Its guides agree.</p>', overview)
        self.assertIn("<summary>Read the rest of the summary</summary><p class=\"prose\">Irf1 is open.</p>", overview)
        self.assertIn('<div class="tab-panel on" role="tabpanel" data-panel="status">', overview)
        self.assertNotIn('data-panel="results"', overview)  # a part with nothing in it has no tab

    def test_once_a_report_is_written_the_summary_is_its_story(self):
        pg = page.fold(None, "pertura_status", {}, {}, [], now=10.0)
        record = {"lead": {"verdict": "complete", "story": "placeholder", "time": "2026-11-13T01:47:51"},
                  "story": "Stat1 lowers the score. Its guides agree."}
        overview = view(page.render(pg, "Pertura · screen_X", record, []), "overview")
        self.assertIn("<b>Summary</b> <span>from the report</span>", overview)
        self.assertIn('<p class="lead-text">Stat1 lowers the score. Its guides agree.</p>', overview)
        self.assertNotIn("placeholder", overview)
        overview = view(page.render(pg, "Pertura · screen_X", {"story": "Stat1 lowers the score."}, []), "overview")
        self.assertIn("Not complete yet</span></div>", overview)  # a story before any review: no time of a review

    def test_the_design_to_confirm_waits_on_the_user_with_its_place(self):
        pg = self.page_with(proposal={"items": [{"item": "Organism", "proposed": "mouse"}]})
        overview = view(page.render(pg, "Pertura · screen_X", {}, []), "overview")
        self.assertIn('<div class="needs"><b>Needs your input</b><ul><li><a href="#design">Confirm the design</a>',
                      overview)

    def test_a_check_not_run_neither_agrees_nor_disagrees(self):
        record = {"findings": {"f1": {"statement": "Stat1 lowers the score", "kind": "gene_set", "checks": {
            "Stat1": {"guides_agree": {"outcome": "agrees"},
                      "within_samples": {"outcome": "not run", "reason": "too few cells"}}}}}}
        card = view(page.render(page.empty(), "Pertura · screen_X", record, []), "finding-f1")
        self.assertIn("Checks agree, 1 not run", card)
        self.assertNotIn("Checks partly agree", card)
        self.assertIn('<i class="sq skip"></i> Not run</span>', card)
        self.assertIn("<h1>Stat1 lowers the score</h1>", card)  # its statement, its id above it
        self.assertIn('<div class="kicker">Finding f1, gene-set score', card)
        self.assertIn('<p class="lede">Stat1 lowers the score</p>', card)

    def test_a_first_look_section_shows_its_own_figure_or_none(self):
        # the run named its file from its own folder, as module runs do; the server path names the run's folder
        pg = self.page_with(figures=[{"file": "map.png", "server": "./figure.png", "run_id": "m1", "what": "states"}])
        record = {"first_look": [
            {"section": "states_map", "label": "The cells' states", "run_id": "m1", "png": "/jobs/m1/figure.png",
             "finding_ids": []},
            {"section": "state_shares", "label": "Shares", "run_id": "m1", "png": "/jobs/m1/figure_composition.png",
             "finding_ids": []}]}
        overview = view(page.render(pg, "Pertura · screen_X", record, []), "overview")
        self.assertEqual(overview.count('src="figures/map.png"'), 1)  # not again for the shares, drawn elsewhere
        self.assertIn('<article class="card fl-card bare">', overview)

    def test_the_plan_says_its_states_in_words_and_a_reason_once(self):
        pg = self.page_with(plan={"next": "Every item of your plan is done", "items": [
            {"id": "p1", "does": "genes", "status": "done", "runs": ["m1"]},
            {"id": "p2", "does": "states", "status": "left", "left_why": "the cells are too few"},
            {"id": "p3", "does": "programmes", "status": "left", "left_why": "the cells are too few"}]})
        plan = view(page.render(pg, "Pertura · screen_X", {}, []), "plan")
        self.assertIn("<h1>Analysis plan</h1>", plan)
        self.assertIn("<td>Completed</td>", plan)
        self.assertEqual(plan.count("the cells are too few"), 1)
        self.assertIn("Same reason as p2", plan)
        self.assertNotIn("Next:", plan)  # nothing is open


class TestTheOverviewsTheRepliesCarry(unittest.TestCase):
    """The cells overview (at the build) and the screen overview (at the first look's end) come inline in the replies
    that register them, so the user's page can show them; a column filled later comes alone and joins its rows."""

    def test_each_overview_is_kept_as_its_latest_and_a_later_column_joins_its_rows(self):
        p = page.fold(None, "build_screen_object", {}, {"cells_overview": {
            "folder": "/s/cells_overview", "clusters": [{"cluster": "C01", "cells": 900}], "cuts": []}}, ["c.png"], 1.0)
        self.assertEqual(p["overviews"]["cells"]["clusters"][0]["cluster"], "C01")
        self.assertEqual(p["overviews"]["cells"]["figure"], "c.png")
        first = {"run_id": "fl1", "screen_overview": {
            "path": "/s/overview", "columns": {"effect": {"state": "filled"}, "genes_changed": {"state": "running"}},
            "rows": [{"target": "A", "cells": 40, "effect": 0.3}, {"target": "B", "cells": 12, "effect": 0.1}]}}
        p = page.fold(p, "module_result", {}, first, ["o.png"], 2.0)
        later = {"screen_overview": {"path": "/s/overview", "columns": {"genes_changed": {"state": "filled"}},
                                     "filled": {"genes_changed": {"A": 31, "B": 0}}}}
        p = page.fold(p, "module_result", {}, later, [], 3.0)
        screen = p["overviews"]["screen"]
        self.assertEqual([r.get("genes_changed") for r in screen["rows"]], [31, 0])
        self.assertEqual(screen["columns"]["genes_changed"]["state"], "filled")
        self.assertEqual((screen["figure"], screen["run_id"]), ("o.png", "fl1"))
        self.assertIn("overviews", page.empty())
        p = page.fold(p, "module_result", {}, {"run_id": "other"}, ["x.png"], 4.0)
        self.assertEqual(p["overviews"]["screen"]["figure"], "o.png")  # another run's figure is not the overview's
        p = page.fold(p, "module_result", {}, {"run_id": "fl1"}, ["fig.png"], 5.0)
        self.assertEqual(p["overviews"]["screen"]["figure"], "fig.png")  # the overview's run opened: its figure
