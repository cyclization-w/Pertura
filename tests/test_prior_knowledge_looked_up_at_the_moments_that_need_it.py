"""Prior knowledge looked up at the moments that need it (docs/adr/0041, step 5). The agent's
memory serves while it explores. A claim (a result, and the structure it names), a finding against what is known, an
observation on the maps and a method of its own behind a result want what is known looked up. In 59 workspaces, 56 of
the 73 priors beside findings named no source but "textbook" or "prior knowledge", 3 named a PMID, and the Sonnet
runs never searched. Code counts a prior as looked up when a search of the session returned its paper, a search that
found nothing is its source, the user gave it, or code read its base. It says so at those moments, lists them for
the research lead and in the report, and blocks nothing."""
from reading_a_report import report_text
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService


def search(rec, query, ids):
    """A literature search as search_literature records it."""
    rec.append("knowledge_recorded", {"question_id": "literature", "source": "Europe PMC", "item": f"search: {query}",
                                      "text": ", ".join(ids) or "none"}, rc.TOOL)


class TestLookedUp(unittest.TestCase):
    def test_a_prior_counts_as_looked_up_only_where_the_session_found_it(self):
        st = {"knowledge": [
            {"question_id": "literature", "source": "Europe PMC", "item": "search: STAT2 BST2",
             "text": "PMID:31234567, DOI:10.1101/2024.08.30.610436", "by": rc.TOOL},
            {"question_id": "G.meaning", "source": "MSigDB hallmark (human)", "item": "the sets", "by": rc.TOOL}]}
        for source, found in (("PMID 31234567", True), ("PMID 39999999", False), ("textbook", False),
                              ("DOI:10.1101/2024.08.30.610436 (a preprint)", True), ("search: STAT2 BST2", True),
                              ("the user", True), ("MSigDB hallmark collection", True), ("prior knowledge", False)):
            self.assertEqual(rc.looked_up(st, {"source": source, "item": source, "by": rc.AGENT}), found, source)


class TestTheMoments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        self.rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        self.rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
        png = os.path.join(self.tmp.name, "f.png")
        open(png, "wb").close()
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": png}, "caption": "c"},
                        rc.TOOL)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def test_a_result_resting_on_memory_says_so_until_its_knowledge_is_looked_up(self):
        self.ok("record_finding", finding_id="F1", statement="STAT2 knockdown lowers BST2 [run:r1].", run_ids=["r1"],
                kind="description", prior={"text": "BST2 is an interferon-stimulated gene", "source": "textbook"})
        out = self.ok("report_result", result_id="R1", title="STAT2 knockdown lowers BST2.", finding_ids=["F1"],
                      figure="fig1", text="STAT2 knockdown lowers BST2 [finding:F1].")
        self.assertIn("nothing known beside this result was looked up", out["known"])
        self.assertIn("your own code", out["known"])  # its one run is a notebook cell, not a module's
        self.assertEqual(out["rests_on"]["prior_looked_up"], 0)
        st = self.rec.state()
        self.assertEqual(rc.known_not_looked_up(st), {"results": ["R1"]})
        self.assertIn(("result without prior knowledge looked up", "R1"),
                      [(i["kind"], i["id"]) for i in rc.critic(st)])
        self.assertEqual(self.ok("read_record", what="coverage")["coverage"]["the_record_as_it_stands"]["open"]["known_not_looked_up"],
                         {"results": ["R1"]})
        out = self.ok("reword_finding", finding_id="F1", reason="the prior from memory",
                      prior={"text": "BST2 is interferon-stimulated", "source": "PMID 39999999"})
        self.assertIn("nothing known beside this result was looked up", out["known"])  # a PMID no search returned
        search(self.rec, "BST2 interferon stimulated gene", ["PMID:31234567"])
        out = self.ok("reword_finding", finding_id="F1", reason="looked up",
                      prior={"text": "BST2 is interferon-stimulated", "source": "PMID 31234567"})
        self.assertNotIn("known", out)
        st = self.rec.state()
        self.assertEqual(rc.known_not_looked_up(st), {})
        self.assertEqual(rc.result_facts(st, st["results"]["R1"])["prior_looked_up"], 1)
        self.assertNotIn("known_not_looked_up", self.ok("read_record", what="coverage")["coverage"])

    def test_a_difference_from_prior_knowledge_asks_for_the_knowledge_it_differs_from(self):
        out = self.ok("record_finding", finding_id="F2", statement="ARID2 raises MHC-II genes [run:r1].",
                      run_ids=["r1"], kind="description",
                      differs={"text": "ARID2 loss is said to lower antigen presentation", "source": "prior knowledge"})
        self.assertIn("differs from prior knowledge", out["known"])
        search(self.rec, "ARID2 MHC class II", [])
        out = self.ok("reword_finding", finding_id="F2", reason="the search found nothing",
                      prior={"text": "no paper found on ARID2 and MHC-II", "source": "search: ARID2 MHC class II"})
        self.assertNotIn("known", out)
        self.assertEqual(rc.known_not_looked_up(self.rec.state()), {})

    def test_an_observation_asks_whether_it_is_expected(self):
        out = self.ok("open_thread", about="two states hold one capture each", raised_by="r1", observation=True)
        self.assertIn("whether it is expected", out["known"])
        first = out["thread_id"]
        search(self.rec, "microglia states by model", ["PMID:30000001"])
        out = self.ok("open_thread", about="the C1Q-high state", raised_by="r1", observation=True,
                      prior={"text": "a C1Q-high microglial state is described", "source": "PMID:30000001"})
        self.assertNotIn("known", out)
        self.assertEqual(rc.known_not_looked_up(self.rec.state()), {"observations": [first]})

    def test_the_report_marks_memory_and_lists_the_result(self):
        self.ok("record_finding", finding_id="F1", statement="STAT2 knockdown lowers BST2 [run:r1].", run_ids=["r1"],
                kind="description", prior={"text": "BST2 is an interferon-stimulated gene", "source": "textbook"})
        self.ok("report_result", result_id="R1", title="STAT2 knockdown lowers BST2.", finding_ids=["F1"],
                figure="fig1", text="STAT2 knockdown lowers BST2 [finding:F1].")
        out = self.ok("write_report")
        self.assertEqual(out["links"]["unlinked"], [])
        text = report_text(out["report"])
        self.assertIn("(textbook; from memory, not looked up)", text)
        self.assertIn("A result whose prior knowledge was not looked up", text.split("## Appendix")[1])


if __name__ == "__main__":
    unittest.main()
