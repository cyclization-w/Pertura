"""The analysis as a tree:
each thread a node under the node it grew from, forks that redo one step with one choice changed, a main line the
research lead or the user moves, the tree in the status, the lead's coverage, the page and each result's path."""
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import page_view
from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestTheTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        self.rec.append("run_logged", {"run_id": "r1", "what": 'module C.genes with {"choices": {"method": "edger_ql"}}',
                                       "code": "c", "inputs": []}, rc.TOOL)
        self.rec.append("run_finished", {"run_id": "r1", "state": "done", "output": "o"}, rc.TOOL)
        self.finding("f1", None)  # the main agent's own, on no thread: the root's
        self.ok("open_thread", about="What S13 is: its markers", raised_by="f1", thread_id="t1", on_line=True)
        self.finding("t1-f1", "t1")
        self.ok("open_thread", about="Each target's genes, a probe", raised_by="r1", thread_id="t2")
        self.finding("t2-f1", "t2")

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def finding(self, fid, thread):
        self.rec.append("finding_recorded", {"finding_id": fid, "statement": f"{fid} [run:r1].", "target": "Rorc",
                                             "feature": "Il17a", "direction": "down", "kind": "gene",
                                             "run_ids": ["r1"], "thread_id": thread}, rc.AGENT)

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def test_each_thread_sits_under_the_node_it_grew_from_and_a_fork_beside_the_step_it_redoes(self):
        # only the main agent opens threads; the lead names them in its review (docs/adr/0050, D3)
        refused, out = self.svc.call("open_thread", {"about": "Up a level: S13 and the genes",
                                                     "raised_by": "one level up", "joins": ["t1-f1", "t2-f1"],
                                                     "thread_id": "t3", "pertura_agent": "pertura:research-lead"})
        self.assertFalse(refused, out)
        self.ok("open_thread", about="Up a level: S13 and the genes", raised_by="one level up",
                joins=["t1-f1", "t2-f1"], thread_id="t3")
        self.ok("open_thread", about="S13 against the other targets", raised_by="t1-f1", fork_of="t1-f1",
                choice="reference: other targets", matters="PF10: another reference, another question",
                thread_id="t4")
        t = rc.tree(self.rec.state())
        self.assertEqual(t["parent"], {"t1": rc.ROOT, "t2": rc.ROOT, "t3": rc.ROOT, "t4": rc.ROOT})
        self.assertEqual(t["line"], [rc.ROOT, "t1"])
        self.assertEqual(t["forks"], ["t4"])
        threads = self.rec.state()["threads"]
        self.assertEqual((threads["t1"]["opened_by"], threads["t3"]["opened_by"]), ("main", "main"))

    def test_a_results_next_question_grows_from_the_deepest_step_it_rests_on(self):
        """GSE267740 (d3a7dcc): r2's next question hung under the root, the node its findings shared, and the main
        line drawn ended there."""
        self.ok("open_thread", about="S13 in each mouse", raised_by="t1-f1", thread_id="t3", on_line=True)
        self.finding("t3-f1", "t3")
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        self.ok("report_result", result_id="res1", title="S13 falls", finding_ids=["t2-f1", "t3-f1"],
                figure="fig1", text="S13 falls [finding:t3-f1] [finding:t2-f1].")
        self.ok("open_thread", about="Does S13 fall in the other model?", raised_by="res1", next_of="res1",
                thread_id="t5", on_line=True)
        t = rc.tree(self.rec.state())
        self.assertEqual(t["parent"]["t5"], "t3")
        self.assertEqual(t["line"], [rc.ROOT, "t1", "t3", "t5"])
        self.finding("t5-f1", "t5")  # the question's own finding joins the result later: t5 stays where it grew
        self.ok("report_result", result_id="res1", finding_ids=["t2-f1", "t3-f1", "t5-f1"],
                text="S13 falls [finding:t3-f1] [finding:t2-f1] [finding:t5-f1].")
        self.assertEqual(rc.tree(self.rec.state())["parent"]["t5"], "t3")

    def test_a_fork_names_its_one_change_and_why(self):
        ok, out = self.svc.call("open_thread", {"about": "a fork", "raised_by": "t1-f1", "fork_of": "t1-f1"})
        self.assertFalse(ok)
        self.assertIn("one choice it changes", str(out))
        ok, out = self.svc.call("open_thread", {"about": "a fork", "raised_by": "t1-f1", "fork_of": "nothing",
                                                "choice": "cells: all", "matters": "PF14"})
        self.assertFalse(ok)

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_a_fork_goes_to_a_worker_with_its_brief_once(self):
        out = self.ok("open_thread", about="S13 against all control cells", raised_by="t1-f1", fork_of="t1-f1",
                      choice="control cells: all of them", matters="PF14: pooled as often as matched",
                      thread_id="t4")
        brief = out["threads_waiting"]["briefs"]["t4"]
        self.assertEqual(brief["worker"], "t4-fork")
        self.assertIn("redoes that step with one change, control cells: all of them", brief["brief"])
        self.assertIn('run r1: C.genes with {"choices": {"method": "edger_ql"}}', brief["brief"])
        self.assertEqual(out["threads_waiting"]["for_workers"], ["t4"])  # one channel: threads_waiting

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_the_lead_suggests_the_main_line_and_the_main_agent_moves_it_once(self):
        """The lead's main line is a suggestion; the main agent or the user moves the line (docs/adr/0050, D3)."""
        out = self.ok("record_review", verdict="not yet", story="S13 falls.", pertura_agent="pertura:research-lead")
        self.assertIn("not answered", out["main_line"])
        self.ok("record_review", verdict="not yet", story="The probe shows more.",
                main_line={"thread_id": "t2", "why": "the genes explain S13's loss"},
                pertura_agent="pertura:research-lead")
        self.assertIsNone(self.rec.state()["main_line"])  # suggested, not moved
        self.assertNotIn("main_line_the_lead_suggests", self.ok("read_record", what="threads"))  # a read: no hints
        said = self.ok("open_thread", about="Next on S13", raised_by="t1-f1", thread_id="t4")
        self.assertEqual(said["main_line_the_lead_suggests"]["thread_id"], "t2")
        moved = self.ok("note_reply", kind="main_line", words="the genes explain S13's loss", thread_id="t2",
                        by="main agent")
        st = self.rec.state()
        self.assertEqual((st["main_line"]["thread_id"], st["main_line"]["by"]), ("t2", "main agent"))
        self.assertEqual(rc.tree(st)["line"], [rc.ROOT, "t2"])
        self.assertEqual(moved["main_line_moved"]["to"], "t2")
        self.assertIn("open the step you were about to take", moved["main_line_moved"]["do"])
        self.assertIn("record on it (thread_id t2)", moved["main_line_moved"]["do"])
        self.finding("t2-f2", "t2")  # the branch grows: the item stays as it was at the move
        again = self.ok("open_thread", about="Another", raised_by="t2-f1", thread_id="t6")
        self.assertNotIn("main_line_moved", again)  # once per move
        self.assertNotIn("main_line_the_lead_suggests", again)  # taken

    def test_the_user_moves_the_main_line_in_their_words(self):
        self.ok("note_reply", kind="main_line", words="follow the genes instead", thread_id="t2")
        st = self.rec.state()
        self.assertEqual((st["main_line"]["by"], st["main_line"]["user_words"]), ("user", "follow the genes instead"))
        ok, out = self.svc.call("note_reply", {"kind": "main_line", "words": "that one"})
        self.assertFalse(ok)

    def test_the_status_the_page_and_the_report_show_the_tree(self):
        self.ok("open_thread", about="S13 against the other targets", raised_by="t1-f1", fork_of="t1-f1",
                choice="reference: other targets", matters="PF10", thread_id="t4")
        text = "\n".join(self.ok("pertura_status")["tree"])
        self.assertIn("★ t1 What S13 is: its markers", text)
        self.assertIn("⑂ t4 S13 against the other targets", text)
        self.assertIn("fork of t1-f1: reference: other targets", text)
        rows = page_view.view(self.rec.state())["tree"]
        self.assertEqual([(r["thread_id"], r["depth"], r["main"]) for r in rows],
                         [("t1", 0, True), ("t2", 0, False), ("t4", 0, False)])
        st = self.rec.state()
        way = rc.result_path(st, {"finding_ids": ["t1-f1"]})
        self.assertEqual(way, {"path": ["t1"], "forks": ["t4"]})
        # a fork of another step is not on this result's path, though both hang off the root
        self.assertEqual(rc.result_path(st, {"finding_ids": ["t2-f1"]}), {"path": ["t2"], "forks": []})
        from pertura.results_report import _path_sentence
        said = _path_sentence(st, way)
        self.assertIn("From the first look: What S13 is: its markers [thread:t1].", said)
        self.assertIn("Beside it, a fork with reference: other targets (open): no finding yet [thread:t4].", said)

    def test_an_ended_fork_stays_in_sight_and_a_former_line_is_marked(self):
        self.ok("open_thread", about="S13 against the other targets", raised_by="t1-f1", fork_of="t1-f1",
                choice="reference: other targets", matters="PF10", thread_id="t4")
        self.ok("thread_status", thread_id="t4", status="set aside", reason="it holds")
        self.ok("thread_status", thread_id="t2", status="set aside", reason="probed")
        self.ok("note_reply", kind="main_line", words="follow the fork", thread_id="t4")
        text = "\n".join(rc.tree_text(self.rec.state()))
        self.assertIn("★ t4 S13 against the other targets", text)  # on the line now, not folded
        self.assertIn("☆ t1 What S13 is", text)  # once on the line

    def test_the_timeline_draws_the_tree_across_time(self):
        """The tree is laid across time."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin",
                                        "status"))
        import page
        self.ok("open_thread", about="S13 against the other targets", raised_by="t1-f1", fork_of="t1-f1",
                choice="reference: other targets", matters="PF10", thread_id="t4")
        record = page_view.view(self.rec.state())
        self.assertEqual({r["thread_id"]: r["parent"] for r in record["tree"]},
                         {"t1": rc.ROOT, "t2": rc.ROOT, "t4": rc.ROOT})
        html = page.render(page.empty(), "Pertura · screen_X", record, [])
        svg = html[html.index("<div class=\"tt\">"):html.index("</svg></div>")]
        self.assertEqual(svg.count("class=\"lane "), 4)  # the main agent's own, t1, t2 and the fork
        self.assertIn("class=\"lane main\"", svg)
        self.assertIn("class=\"lane fork\"", svg)
        self.assertIn("changes reference: other targets", svg)
        self.assertIn("★ t1 What S13 is", svg)
        self.assertLess(svg.index("★ t1"), svg.index("⑂ t4"))  # the main line on top

    def test_the_tree_text_folds_ended_leaves_and_stays_short(self):
        for i in range(60):
            self.ok("open_thread", about=f"probe {i}", raised_by="r1", thread_id=f"p{i}")
        lines = rc.tree_text(self.rec.state())
        self.assertLessEqual(len(lines), rc.TREE_LINES + 1)
        self.assertIn("more nodes", lines[-1])


if __name__ == "__main__":
    unittest.main()
