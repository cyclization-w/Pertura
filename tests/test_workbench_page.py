"""The user's page as a workbench (development session 6, problem 11): what the agent did last and why, the runs in
flight, the threads with their steps and findings, each step's code and output, the doubts with their answers, and
the steps in order as a notebook beside the page."""
import base64
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
if not os.path.isdir(STATUS):  # the plugin is not deployed to the analysis server
    raise unittest.SkipTest("no plugin folder here")
sys.path.insert(0, STATUS)
import page  # noqa: E402
import status_file as sf  # noqa: E402

PREFIX = "mcp__plugin_pertura_pertura__"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PROJECT = "screen_X-1a2b3c4d"
KEY = sf.project_key(f"/ws/{PROJECT}")  # the project's status file and page folder


def call(tool, tool_input, reply, images=0):
    content = [{"type": "text", "text": json.dumps(reply)}]
    content += [{"type": "image", "data": base64.b64encode(PNG).decode("ascii"), "mimeType": "image/png"}
                for _ in range(images)]
    return {"session_id": "s1", "tool_name": PREFIX + tool, "tool_input": tool_input, "tool_response": content,
            "cwd": "."}


class TestTheWorkbench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = 1_800_000_000.0
        self.call("open_project", {"data_folder": "/data/X"}, {"project": f"/ws/{PROJECT}"})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool, args, reply, images=0):
        self.now += 60
        return sf.record_call(call(tool, args, reply, images), self.root, now=self.now)

    def folder(self):
        return os.path.join(self.root, "projects", KEY)

    def text(self):
        with open(os.path.join(self.folder(), page.PAGE_NAME), encoding="utf-8") as fh:
            return fh.read()

    def section(self, text, vid):
        """One view of the page, by its id."""
        part = text[text.index(f'<section class="view" id="{vid}"'):]
        return part[:part.index("</section>")]

    def a_thread_with_a_step_a_finding_and_a_doubt(self):
        self.call("open_thread", {"about": "SPI1-a moves cells into cluster 1", "raised_by": {"run": "m1"}},
                  {"thread_id": "t1", "status": "open"})
        self.call("run_code", {"what": "cluster 1's markers", "why": "what the state is", "thread_id": "t1",
                               "code": "markers = rank(adata, 'cluster')\nprint(markers.head())"},
                  {"run_id": "run0001-aa", "status": "ok", "stdout": "TXNIP np.float64(2.31)\nYPEL3 np.float64(1.9)\n"},
                  images=1)
        self.call("record_finding", {"finding_id": "f1", "statement": "Cluster 1 is marked by TXNIP and YPEL3.",
                                     "run_ids": ["run0001-aa"], "thread_id": "t1"}, {"finding_id": "f1"})
        self.call("raise_doubt", {"doubt_id": "d1", "finding_id": "f1", "text": "Cluster 1 may be low depth.",
                                  "would_change": "the markers at matched depth"}, {"doubt_id": "d1"})
        self.call("answer_doubt", {"doubt_id": "d1", "outcome": "holds", "reason": "the same markers at matched depth",
                                   "run_ids": ["run0002-bb"]}, {"doubt_id": "d1"})

    def test_a_thread_lists_its_steps_and_findings_and_a_step_holds_why_its_code_and_its_output(self):
        self.a_thread_with_a_step_a_finding_and_a_doubt()
        text = self.text()
        threads = self.section(text, "threads")
        self.assertIn("SPI1-a moves cells into cluster 1", threads)
        self.assertIn("Raised by: run m1", threads)
        self.assertIn('href="#result-run0001-aa">cluster 1&#x27;s markers</a>', threads)  # its step, linked
        self.assertIn('href="#finding-f1"', threads)
        results = self.section(text, "timeline")
        self.assertIn("Why: what the state is", results)
        self.assertIn("<summary>Code</summary><pre><code>markers = rank(adata, &#x27;cluster&#x27;)", results)
        self.assertIn("<summary>Output</summary><pre>TXNIP 2.31\nYPEL3 1.9", results)
        self.assertNotIn("np.float64", text)  # numbers as a reader reads them
        self.assertIn('on <a href="#thread-t1">', results)

    def test_a_findings_doubts_fold_under_a_count_of_their_answers(self):
        self.a_thread_with_a_step_a_finding_and_a_doubt()
        self.call("raise_doubt", {"doubt_id": "d2", "finding_id": "f1", "text": "One guide only?",
                                  "would_change": "each guide alone"}, {"doubt_id": "d2"})
        self.call("raise_doubt", {"doubt_id": "d3", "text": "The captures differ in depth.",
                                  "would_change": "depth by capture"}, {"doubt_id": "d3"})  # on the whole analysis
        findings = self.section(self.text(), "findings")
        card = self.section(self.text(), "finding-f1")
        self.assertIn("Concerns <b>2</b>: 1 ruled out, 1 not checked yet", card)  # by their answers, in words
        self.assertIn('Cluster 1 may be low depth. <span class="muted">(d1,', card)
        self.assertIn("<b>Would change the conclusion:</b> the markers at matched depth", card)
        self.assertIn("Ruled out</span>the same markers at matched depth", card)
        self.assertIn("General concerns", findings)
        self.assertIn('The captures differ in depth. <span class="muted">(d3,', findings)

    def test_a_narrowed_finding_and_each_kind_of_limitation_read_in_words(self):
        self.a_thread_with_a_step_a_finding_and_a_doubt()
        self.call("answer_doubt", {"doubt_id": "d1", "outcome": "holds, narrowed", "reason": "only at high depth",
                                   "run_ids": ["run0003-cc"]}, {"doubt_id": "d1"})
        for did, limit in (("d2", "design"), ("d3", "budget"), ("d4", "tool")):
            self.call("raise_doubt", {"doubt_id": did, "finding_id": "f1", "text": f"Concern {did}."}, {"doubt_id": did})
            self.call("answer_doubt", {"doubt_id": did, "outcome": "stated as a limit", "limit": limit,
                                       "reason": f"why {did}"}, {"doubt_id": did})
        card = self.section(self.text(), "finding-f1")
        self.assertIn('<i class="sq part"></i> Narrows the finding</span>only at high depth', card)
        self.assertIn("Noted as a limitation: the design cannot answer it</span>why d2", card)
        self.assertIn('<i class="sq open"></i> Left unchecked for time or cost</span>why d3', card)  # a check owed
        self.assertIn("Noted as a limitation: no tool here does it</span>why d4", card)
        self.assertIn("Concerns <b>4</b>: 1 narrows the finding, 2 noted as limitations, "
                      "1 left unchecked for time or cost", card)

    def test_the_threads_code_opened_reach_the_page_with_their_moves_and_an_ended_one_goes_last(self):
        shown = {"open": [{"thread_id": "C.any_effect:GATA1-i", "status": "open",
                           "about": "GATA1-i's cells differ from the control cells beyond the control-guide groups",
                           "moves": [{"id": "T1", "run": "F.guides_agree"}, {"id": "O4"}]}]}
        self.call("module_result", {"run_id": "m1"}, {"run_id": "m1", "state": "done", "job_id": "m1",
                                                    "summary": "8 targets past the calibration", "threads": shown})
        self.call("open_thread", {"about": "the user's question", "raised_by": "the user"},
                  {"thread_id": "t2", "status": "open"})
        self.call("thread_status", {"thread_id": "t2", "status": "set aside", "reason": "answered by F1"},
                  {"thread_id": "t2", "status": "set aside", "findings": []})
        self.call("wait_for_runs", {}, {"ended": [], "open_threads": shown})  # shown again: its place stays
        threads = self.section(self.text(), "threads")
        self.assertIn("GATA1-i&#x27;s cells differ", threads)
        self.assertEqual(re.findall(r'href="knowledge.html#([^"]+)"', threads), ["T1", "O4"])
        self.assertIn("Suggested next steps", threads)
        self.assertIn("Closed because: answered by F1", threads)
        self.assertLess(threads.index("C.any_effect:GATA1-i"), threads.index("(t2)"))  # open before ended

    def test_now_says_the_last_step_and_why_and_the_runs_in_flight_in_words(self):
        self.call("run_module", {"module": "B.guide_calls_and_their_evidence", "why": "which cells carry a guide"},
                  {"run_id": "m2", "state": "running", "job_id": "m2"})
        text = self.text()
        now = self.section(text, "overview")
        self.assertIn("Latest step: <b>Guide calls and their evidence</b>", now)
        self.assertIn("Why: which cells carry a guide", now)
        self.assertIn("<li>Guide calls and their evidence", now)  # running, in words
        self.assertNotIn("B.guide_calls", text)
        self.call("module_result", {"run_id": "m2"}, {"run_id": "m2", "state": "done", "job_id": "m2",
                                                    "summary": "20,717 cells with one target",
                                                    "digest_line": "on no thread yet · 0 findings"})
        now = self.section(self.text(), "overview")
        self.assertNotIn("Running:", now)
        self.assertNotIn("on no thread yet", now)  # the server's status line is the terminal's; the page has its parts
        results = self.section(self.text(), "timeline")
        self.assertIn("Why: which cells carry a guide", results)  # the run's why reaches its result

    def test_a_module_run_that_failed_reads_as_failed_with_its_last_line_as_a_cell_does(self):
        self.call("run_module", {"module": "B.efficacy"}, {"run_id": "m4", "state": "running", "job_id": "m4"})
        self.call("module_result", {"run_id": "m4"}, {"run_id": "m4", "state": "failed", "job_id": "m4",
                                                    "run": "failed", "output": "Traceback\nValueError: no cells"})
        timeline = self.section(self.text(), "timeline")
        self.assertIn("<p>failed: ValueError: no cells</p>", timeline)
        self.assertIn('<div class="rail"><i class="sq bad"></i></div>', timeline)

    def test_the_notebook_beside_the_page_holds_the_steps_in_order(self):
        self.a_thread_with_a_step_a_finding_and_a_doubt()
        self.call("run_code", {"what": "a second cell", "code": "x = 1\nx"}, {"run_id": "run0003-cc", "status": "error",
                                                                          "error": "NameError: y"})
        self.call("module_result", {"run_id": "m3"}, {"run_id": "m3", "state": "done", "job_id": "m3",
                                                    "summary": "4 guides"})
        with open(os.path.join(self.folder(), page.NOTEBOOK_NAME), encoding="utf-8") as fh:
            nb = json.load(fh)
        self.assertEqual((nb["nbformat"], nb["nbformat_minor"]), (4, 4))
        kinds = [c["cell_type"] for c in nb["cells"]]
        self.assertEqual(kinds, ["markdown", "code", "markdown", "code", "markdown"])
        first, code = "".join(nb["cells"][0]["source"]), nb["cells"][1]
        self.assertIn("**cluster 1's markers**", first)
        self.assertIn("on thread t1: SPI1-a moves cells into cluster 1", first)
        self.assertIn("Why: what the state is", first)
        figure = os.listdir(os.path.join(self.folder(), page.FIGURES))[0]
        self.assertIn(f"]({page.FIGURES}/{figure})", first)
        self.assertEqual("".join(code["source"]), "markers = rank(adata, 'cluster')\nprint(markers.head())")
        self.assertEqual("".join(code["outputs"][0]["text"]), "TXNIP 2.31\nYPEL3 1.9")
        self.assertIn("NameError: y", "".join(nb["cells"][3]["outputs"][0]["text"]))
        self.assertIn("4 guides", "".join(nb["cells"][4]["source"]))  # a module's run: its result in a line

    def test_the_notebook_reads_in_a_browser_as_a_page_of_the_same_steps(self):
        self.a_thread_with_a_step_a_finding_and_a_doubt()
        self.call("run_code", {"what": "a second cell", "code": "x = 1\nx"}, {"run_id": "run0003-cc", "status": "error",
                                                                          "error": "NameError: y"})
        self.assertIn(f'<a href="{page.NOTEBOOK_PAGE}">', self.text())  # the sidebar opens the page, not the .ipynb
        with open(os.path.join(self.folder(), page.NOTEBOOK_PAGE), encoding="utf-8") as fh:
            nb = fh.read()
        self.assertIn('<div class="nb-prompt">In [', nb)
        self.assertIn('<pre class="nb-code"><code>markers = rank(adata, <span class="s">&#x27;cluster&#x27;</span>)', nb)
        self.assertIn('<pre class="nb-output">TXNIP 2.31', nb)
        self.assertIn(f'on <a href="{page.PAGE_NAME}#', nb)  # its thread, on the page
        self.assertIn(f'href="{page.FIGURES}/', nb)
        failed = nb[nb.index('<section class="nb-cell failed"'):]
        self.assertIn('<i class="sq bad"></i> a second cell', failed)  # the cell that failed, by its last line
        self.assertIn('<details class="nb-more"><summary>The code and the error</summary>', failed)  # then folded
        self.assertLess(nb.index("cluster 1&#x27;s markers"), nb.index("a second cell"))  # oldest first

    def test_a_cells_code_is_coloured_by_pythons_tokenizer_and_other_code_left_plain(self):
        self.assertEqual(page._highlight("def f(x):\n    return len(x)  # n\n"),
                         '<span class="k">def</span> <span class="f">f</span>(x):\n    <span class="k">return</span> '
                         '<span class="b">len</span>(x)  <span class="c"># n</span>\n')
        self.assertEqual(page._highlight('x = """open'), "x = &quot;&quot;&quot;open")  # no tokens: as it is

    def test_a_cell_keeps_at_most_its_cut_of_code_and_output(self):
        self.call("run_code", {"what": "long", "code": "a" * (page.CODE_KEEP + 50)},
                  {"run_id": "run0004-dd", "status": "ok", "stdout": "b" * (page.OUTPUT_KEEP + 10)})
        st = sf.read_json(sf.project_path(KEY, self.root))
        x = st["page"]["results"][0]
        self.assertTrue(x["code"].endswith("(50 more characters)"))
        self.assertTrue(x["output"].endswith("(10 more characters)"))


class TestReplacingAFile(unittest.TestCase):
    def test_a_file_held_for_a_moment_is_replaced_on_a_later_try(self):
        real = os.replace
        tries = []

        def held_once(src, dst):
            tries.append(dst)
            if len(tries) == 1:
                raise PermissionError("held by a reader")
            return real(src, dst)

        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "a.tmp"), os.path.join(d, "a")
            with open(src, "w") as fh:
                fh.write("x")
            with mock.patch.object(page.os, "replace", held_once), mock.patch.object(page.time, "sleep"):
                page.replace_file(src, dst)
            self.assertEqual(len(tries), 2)
            with open(dst) as fh:
                self.assertEqual(fh.read(), "x")


if __name__ == "__main__":
    unittest.main()
