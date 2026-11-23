"""The routes of published screens in view where the agent plans (the user's decision of 2026-11-02: the field's
analysis chains in the replies at the plan). Session 11 never read card P2, whose score from the design's own contrast
gives its paper's main findings; its continuation ran its own threads and still not that score. Now write_plan's reply
carries each card the design allows, as its steps in one line; the card's controls and forks stay in its text."""
import json
import os
import tempfile
import unittest
from unittest import mock

from pertura import paradigms
from pertura.analysis_service import AnalysisService


class TestChains(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = paradigms.load()

    def test_a_design_with_controls_and_conditions_gets_the_cards_it_allows_with_their_steps(self):
        found = paradigms.chains(self.cards, {"n_control_guides": 9, "n_conditions": 4, "modes": ["crispri"]})
        ids = [c.split(",")[0] for c in found]
        self.assertEqual(ids, ["P1", "P2", "P3"])  # P6 needs pairs
        p2 = found[1]
        self.assertIn("score from a reference contrast", p2)
        self.assertIn("[conditions]", p2)  # each perturbation's score in each condition, by the tool that runs it
        self.assertIn("default: the contrast the design holds", p2)
        self.assertIn("1. choose the contrast", p2)

    def test_pairs_add_the_combinations_card_and_too_few_controls_allow_none(self):
        with_pairs = paradigms.chains(self.cards, {"n_control_guides": 8, "pairs": True})
        self.assertIn("P6", [c.split(",")[0] for c in with_pairs])
        self.assertEqual(paradigms.chains(self.cards, {"n_control_guides": 2, "n_conditions": 4}), [])



class TestTheMapMoment(unittest.TestCase):
    """The map (A.screen_overview) comes in and no plan is written yet: the state list gives the main agent the same
    routes, so it sees them before it plans, not only after. It is the state list's item, given once and to the main
    agent alone (docs/adr/0050, D2), no longer the overview run's reply, which any reader of the run got each time."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.svc.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.svc.project)
        self.svc.design = mock.Mock(cfg={})
        self.svc.record = mock.Mock()
        self.state = {"runs": {"m1": {"run_id": "m1", "job_id": "m1", "state": "done", "what": "the map",
                                      "question_id": "A.screen_overview"}}, "plan": None, "knowledge": []}
        self.svc.record.state.side_effect = lambda: self.state
        folder = os.path.join(self.svc._jobs_root(), "m1")
        os.makedirs(folder)
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": "measured", "summary": "28 targets"}, fh)

    def tearDown(self):
        self.svc.record = None
        self.svc.close()
        self.tmp.cleanup()

    def maps(self):
        return self.svc._steering({}).get("the_maps_are_in")

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_the_maps_moment_carries_the_routes_until_a_plan_is_written(self):
        facts = {"n_control_guides": 9, "n_conditions": 4}
        with mock.patch.object(self.svc, "_design_facts", return_value=facts):
            self.assertIn("P2", [c.split(",")[0] for c in self.maps()["published_chains_this_design_allows"]])
            self.assertNotIn("published_chains_this_design_allows", self.svc.module_result("m1"))  # not the run's
            self.state["plan"] = {"items": {"1": {}}, "left": {}}
            self.assertIsNone(self.maps())

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_the_maps_moment_names_the_measurements_no_run_has_read(self):
        os.makedirs(self.svc._screen_dir())
        with open(os.path.join(self.svc._screen_dir(), "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"antibody_features": ["CD25", "CD127", "Resting_Treg"]}, fh)
        with mock.patch.object(self.svc, "_design_facts", return_value={"n_control_guides": 9}):
            said = self.maps()["data_no_run_has_read"][0]
            self.assertIn("3 antibody features", said)
            self.assertIn("CD25", said)
            self.state["runs"]["c1"] = {"run_id": "c1", "state": "done", "what": "a cell",
                                        "code": "a = pt.screen().adata.obsm['antibody_counts']"}
            self.assertNotIn("data_no_run_has_read", self.maps())


if __name__ == "__main__":
    unittest.main()
