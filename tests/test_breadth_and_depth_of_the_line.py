"""Breadth and depth of the line (2026-11-07, P17, P6 and P15). Of the points lost on eight screens, 62% were never
looked at: the states read by their biology, what a target's genes are, the conditions apart; the lines started
from the largest effect. Of the threads that followed a finding, 68% checked it again, and the levels counted
figures to draw.
- P17: code lists the kinds of conclusion the design can reach, each with why it applies;
- P6: a thread reading a kind gets a worker's brief, for workers sent all at once;
- P15: a thread's step says what it is to the line, and a result's depth counts its chain of new questions."""
import json
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import make_deposit

from pertura import candidates
from pertura import record as rc
from pertura.analysis_service import AnalysisService


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("r1", "r2", "r3"):
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def refused(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertFalse(ok, out)
        return out["error"]


class TestTheLinesDepthCountsNewQuestions(Service):
    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_a_check_between_two_new_questions_adds_no_level(self):
        self.ok("open_thread", thread_id="t1", about="RAF1 lowers the IEGs", raised_by="the user's question",
                step="new question")
        self.ok("record_finding", finding_id="f1", statement="RAF1 lowers EGR1 [run:r1].", run_ids=["r1"],
                kind="description", thread_id="t1")
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="batch", would_change="gone within a batch")
        out = self.ok("open_thread", thread_id="t2", about="the same within each batch", raised_by="f1",
                      doubt_id="d1")
        self.assertEqual(self.rec.state()["threads"]["t2"]["step"], "check")  # a doubt's thread is a check
        self.assertEqual(out["threads_waiting"]["briefs"]["t2"]["worker"], "t2-doubt")  # its brief, in the notice
        self.ok("record_finding", finding_id="f2", statement="it holds in each batch [run:r2].", run_ids=["r2"],
                kind="description", thread_id="t2")
        self.ok("open_thread", thread_id="t3", about="EGR1's targets fall with it", raised_by="f2",
                step="new question")
        self.ok("record_finding", finding_id="f3", statement="EGR1's targets fall [run:r3].", run_ids=["r3"],
                kind="description", thread_id="t3")
        st = self.rec.state()
        self.assertEqual(rc.new_questions_behind(st, ["f3"]), (2, ["t1", "t3"]))
        self.assertEqual(rc.new_questions_behind(st, ["f2"]), (1, ["t1"]))
        self.rec.append("figure_saved", {"figure_id": "fig3", "run_id": "r3", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        out = self.ok("report_result", result_id="R1", title="RAF1 lowers EGR1 and its targets.",
                      finding_ids=["f3"], figure="fig3", text="It does [finding:f3].")
        self.assertEqual(out["rests_on"]["new_questions_behind_it"], 2)
        cov = self.ok("read_record", what="coverage")["coverage"]
        self.assertEqual(cov["the_record_as_it_stands"]["open"]["new_questions_behind_each_result"]["R1"]["new_questions"], 2)
        self.assertNotIn("steps_of_the_threads", cov)  # housekeeping, not in the record as it stands

    def test_a_step_is_one_of_its_list(self):
        self.assertIn("step is one of", self.refused("open_thread", about="x", raised_by="r1", step="deeper"))


class TestEachKindOfConclusionGoesToAWorker(Service):
    def test_a_thread_reading_a_kind_is_a_new_area_with_its_workers_brief(self):
        out = self.ok("open_thread", thread_id="t5", about="the programmes of every target", raised_by="the design",
                      reads="programmes", on_line=False)
        th = self.rec.state()["threads"]["t5"]
        self.assertEqual((th["kind"], th["step"], th["reads"]), (rc.BREADTH, "new area", "programmes"))
        self.assertIn("`scores` (gene_sets hallmark)", out["worker"]["brief"])
        self.assertIn("every target", out["worker"]["brief"])
        self.assertIn("they can run in parallel", out["launch"])  # docs/adr/0049, D6
        self.assertIn("kind of conclusion", self.refused("open_thread", about="x", raised_by="r1", reads="genes"))
        self.assertIn(("t5", "open"), rc.lead_threads_without_an_end(self.rec.state()))  # it ends with its evidence

    def test_the_kinds_follow_the_designs_facts(self):
        design = {"experiment": {"organism": {"value": "human", "status": "confirmed"},
                                 "conditions": {"value": {"name": "time", "levels": ["6h", "18h"]},
                                                "status": "confirmed"}}}
        screen = os.path.join(self.tmp.name, "screen")
        os.makedirs(screen)
        with open(os.path.join(screen, "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"antibody_features": []}, fh)
        self.ok("open_thread", thread_id="t6", about="the states", raised_by="the design", reads="states")
        with mock.patch.object(self.svc, "_confirmed", return_value=design), \
                mock.patch.object(self.svc, "_screen_dir", return_value=screen):
            kinds = {k["kind"]: k for k in self.svc._kinds_of_conclusion(self.rec.state())}
        self.assertEqual(list(kinds), list(candidates.KINDS))
        self.assertTrue(kinds["programmes"]["applies"])
        self.assertTrue(kinds["conditions_apart"]["applies"], kinds["conditions_apart"]["why"])
        self.assertIn("6h", kinds["conditions_apart"]["why"])
        self.assertFalse(kinds["second_readout"]["applies"])
        self.assertIsNone(kinds["targets_on_each_other"]["applies"])  # known once the overview has run
        self.assertEqual(kinds["states"]["thread_ids"], ["t6"])

    def test_without_a_confirmed_design_no_kind_is_said(self):
        kinds = self.svc._kinds_of_conclusion(self.rec.state())
        self.assertTrue(all(k["applies"] is None for k in kinds))


if __name__ == "__main__":
    unittest.main()


class TestTheLeadStartsFromWhatIsNotExplained(Service):
    """The build goal of 2026-11-11, step 3: on GSE263761 (dc5b321) the survey had run the states and the conditions,
    and the lead sent workers to run them again as kinds no thread read; its coverage led with the check most likely to
    change a result. A kind the survey ran names its run, and the coverage leads with the observations not yet
    explained, the strongest first."""

    def test_a_kind_the_survey_ran_names_its_run(self):
        rows = [{"id": "states", "row": "states", "run_id": "r1", "state": "done"},
                {"id": "effect_changing:6h vs 24h", "row": "effect_changing", "run_id": "r2", "state": "done"},
                {"id": "distance:genes", "row": "distance", "run_id": "r3", "state": "done"}]
        with mock.patch.object(self.svc, "_survey_on", return_value=True), \
                mock.patch.object(self.svc, "_survey_rows", return_value=rows):
            kinds = {k["kind"]: k for k in self.svc._kinds_of_conclusion(self.rec.state())}
        self.assertEqual(kinds["states"]["survey_run_ids"], ["r1"])
        self.assertEqual(kinds["conditions_apart"]["survey_run_ids"], ["r2"])
        self.assertNotIn("survey_run_ids", kinds["targets_on_each_other"])  # the changed genes are not the network

    def test_the_observations_not_yet_explained_lead_the_coverage(self):
        self.ok("open_thread", thread_id="o1", about="Dnmt1 changes 333 genes, 332 up", raised_by="r1",
                observation=True)
        self.ok("open_thread", thread_id="o2", about="Spi1 raises the ISGs", raised_by="r2", observation=True)
        self.ok("thread_status", thread_id="o2", status="set aside", reason="one guide carries it")
        self.ok("open_thread", thread_id="l1", about="is the ISG rise direct or from lost identity?",
                raised_by="one level up", joins=["f1"] if "f1" in self.rec.state()["findings"] else [])
        cov = self.ok("read_record", what="coverage")["coverage"]
        self.assertEqual({o["thread_id"] for o in cov["the_record_as_it_stands"]["open"]["observations_not_yet_explained"]}, {"o1", "l1"})
        self.assertNotIn("the_check_most_likely_to_change_the_main_result", cov)
