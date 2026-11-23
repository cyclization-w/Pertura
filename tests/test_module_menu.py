"""The module menu (agreed 2026-11-05): code reads from the screen object and the design which modules can run now,
which wait for another module and which this screen cannot run; the module picker (Haiku) records a few picks with
its reasons; the user ticks the ones to run on a form, each one ticked starts, and the choice is recorded. Six modules
had never run in 18 Pertura runs (D.cutting_response among them, on GSE250558 with both kinds of control)."""
import gzip
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

from test_design_service import make_deposit

from pertura import question_modules as qm
from pertura import record as rc
from pertura.analysis_service import AnalysisError, AnalysisService
from pertura.question_modules import applicability as ap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis", "end_to_end"))


def node(v):
    return {"value": v, "status": "confirmed"}


def screen_tables(folder, kinds=("non_targeting", "safe_targeting"), conditions=("rest", "stim")):
    """cells.tsv.gz and guides.tsv of a small screen: targets A, B, C (two guides each) and control cells of the kinds
    given, over two units and the conditions given."""
    rows = []
    for i in range(240):
        target = "ABC"[i % 3] if i < 180 else "control"
        kind = kinds[i % len(kinds)] if target == "control" else ""
        rows.append({"label": target, "unit": f"u{i % 2}", "condition": conditions[i % len(conditions)],
                     "control_kind": kind})
    with gzip.open(os.path.join(folder, "cells.tsv.gz"), "wt") as fh:
        pd.DataFrame(rows).to_csv(fh, sep="\t", index=False)
    guides = [{"guide": f"{t}_{n}", "target": t, "kind": "targeting", "cells": 30} for t in "ABC" for n in (1, 2)]
    guides += [{"guide": f"NT_{k}", "target": "control", "kind": k, "cells": 30} for k in kinds]
    pd.DataFrame(guides).to_csv(os.path.join(folder, "guides.tsv"), sep="\t", index=False)


DESIGN = {"perturbation": {"modality": node("crispri")},
          "experiment": {"context": node("in_vivo"), "organism": node("mouse"),
                         "conditions": node([{"name": "stimulus", "levels": ["rest", "stim"]}])}}


class TestWhichModulesCanRun(unittest.TestCase):
    def test_every_module_has_its_test(self):
        self.assertEqual(sorted(set(qm.REGISTRY) - set(ap.TESTS)), [])

    def test_the_states_on_a_small_screen(self):
        with tempfile.TemporaryDirectory() as d:
            screen_tables(d)
            f = ap.facts(d, DESIGN, done=["B.efficacy"])
            state = {r["module"]: r["state"] for r in ap.menu(f)}
            self.assertEqual(f["targets"], 3)
            self.assertEqual(f["control_cells_by_kind"], {"non_targeting": 30, "safe_targeting": 30})
            for m in ("D.cutting_response", "E.condition_x_perturbation", "B.clonality", "B.dose", "F.guides_agree",
                      "F.replicates_agree", "D.common_response", "C.cell_cycle"):
                self.assertEqual(state[m], "now", m)
            for m in ("C.within_states", "G.meaning", "E.program_axis", "A.screen_overview"):
                self.assertEqual(state[m], "after", m)
            for m in ("E.combinations", "E.opposite_modes", "B.vector_structure"):
                self.assertEqual(state[m], "no", m)
            screen_tables(d, kinds=("non_targeting",), conditions=("rest",))
            f = ap.facts(d, DESIGN, done=["C.genes", "C.states_and_composition"], state_shifts=0)
            row = {r["module"]: r for r in ap.menu(f)}
            self.assertEqual(row["D.cutting_response"]["state"], "no")  # one kind of control
            self.assertIn("non_targeting 60", row["D.cutting_response"]["reason"])
            self.assertEqual(row["E.condition_x_perturbation"]["state"], "no")
            self.assertEqual((row["C.within_states"]["state"], row["G.meaning"]["state"]), ("no", "now"))

    def test_the_measured_run_times_are_there(self):
        t = ap.typical_minutes()
        self.assertGreater(t["D.common_response"]["stopped_or_failed"], 0)
        self.assertTrue({"median_minutes", "runs", "longest_minutes"} <= set(t["C.genes"]))


class TestTheMenuThroughTheService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        os.makedirs(self.svc._screen_dir(), exist_ok=True)
        screen_tables(self.svc._screen_dir())
        open(os.path.join(self.svc._screen_dir(), "screen.json"), "w").write("{}")
        self.overview = {OVERVIEW: [{"run_id": "ov", "state": "done"}]}
        for name, value in (("_confirmed", DESIGN), ("_first_look_sections", []),
                            ("_candidate_structures", {}), ("_module_runs", self.overview)):
            patcher = mock.patch.object(AnalysisService, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def test_the_brief_records_what_it_offered(self):
        out = self.svc.module_menu()
        offered = [r["module"] for r in out["can_run_now"]]
        self.assertIn("D.cutting_response", offered)
        self.assertIn("E.condition_x_perturbation", [r["module"] for r in out["can_run_now"]])
        self.assertTrue(all("returns" in r for r in out["can_run_now"]))
        self.assertEqual({(r["tool"], r["view"]) for r in out["can_run_now"] if r["module"] == "D.cutting_response"},
                         {("guides", "cutting")})  # each analysis as the tool call that starts it (ADR 0044, D4)
        self.assertTrue(any("stopped or failed" in r.get("typical", "") for r in out["can_run_now"]))
        given = [m for m in self.rec.state()["module_menus"] if m["kind"] == "module_menu_given"]
        self.assertEqual(given[-1]["modules"], offered)

    def test_picks_need_their_reasons_and_known_modules(self):
        with self.assertRaises(AnalysisError):
            self.svc.record_module_picks([{"module": "D.cutting_response"}])
        with self.assertRaises(AnalysisError):
            self.svc.record_module_picks([{"module": "D.no_such", "why": "x"}])
        with self.assertRaises(AnalysisError):
            self.svc.record_module_picks([{"module": "C.genes", "why": "x"}] * 6)
        self.assertEqual(self.svc.record_module_picks([])["picks"], [])  # nothing open: said, not looped
        for bad in ({"tool": "no_such", "why": "x"}, {"tool": "states", "view": "no_such", "why": "x"}):
            with self.assertRaises(AnalysisError):
                self.svc.record_module_picks([bad])

    def test_picks_are_tool_calls_and_run_as_them(self):
        """ADR 0044, D4: the picker writes tool calls; a pick naming a module is its tool and view."""
        out = self.svc.record_module_picks([
            {"tool": "states", "view": "within", "cells": {"condition": "stim"}, "why": "doubt d1"},
            {"module": "D.cutting_response", "why": "two kinds of control"},
            {"tool": "scores", "contrast": {"column": "condition", "levels": ["rest", "stim"]}, "why": "d2"}])
        self.assertEqual([(p["tool"], p["view"], p["module"]) for p in out["picks"]],
                         [("states", "within", "C.within_states"), ("guides", "cutting", "D.cutting_response"),
                          ("scores", "scores", "C.programs")])
        self.assertEqual(out["picks"][0]["cells"], {"condition": "stim"})
        with mock.patch.object(AnalysisService, "run_kind", return_value={"run_id": "r9"}) as started:
            self.svc.offer_analyses(elicit=lambda message, schema: ("accept", {f"run_{i}": True for i in range(3)}))
        self.assertEqual([c.args[:2] for c in started.call_args_list],
                         [("states", "within"), ("guides", "cutting"), ("scores", "scores")])
        self.assertEqual(started.call_args_list[2].kwargs["contrast"]["levels"], ["rest", "stim"])  # its own
        self.assertEqual(started.call_args_list[0].kwargs["cells"], {"condition": "stim"})

    def test_the_user_ticks_and_only_the_ticked_start(self):
        self.assertIn("launch the module picker", self.svc._menu_due(self.rec.state()))
        self.svc.record_module_picks([{"module": "D.cutting_response", "why": "safe-targeting and non-targeting "
                                       "controls never compared"},
                                      {"module": "C.within_states", "why": "doubt d1: shares or within cells"}])
        self.assertIn("offer_analyses", self.svc._menu_due(self.rec.state()))
        self.assertIn("each one they choose with its tool", self.svc.offer_analyses()["next"])  # no form here
        asked = []

        def elicit(message, schema):
            asked.append(schema)
            return "accept", {"run_0": True, "run_1": False}
        with mock.patch.object(AnalysisService, "run_module", return_value={"run_id": "r9"}) as started:
            out = self.svc.ask_module_menu(elicit=elicit)
        self.assertEqual(out["chosen"], ["D.cutting_response"])
        self.assertEqual(started.call_args[0][0], "D.cutting_response")
        self.assertEqual(asked[0]["properties"]["run_0"]["type"], "boolean")
        answered = [m for m in self.rec.state()["module_menus"] if m["kind"] == "module_menu_answered"][-1]
        self.assertEqual((answered["offered"], answered["chosen"]),
                         (["D.cutting_response", "C.within_states"], ["D.cutting_response"]))
        self.assertIsNone(self.svc._menu_due(self.rec.state()))
        self.rec.append("lead_reviewed", {"verdict": "not yet", "story": "s"}, rc.AGENT)
        self.assertIn("launch the module picker", self.svc._menu_due(self.rec.state()))  # after each review

    def test_the_pages_view_groups_each_menu_with_its_picks_and_the_users_choice(self):
        from pertura import page_view
        self.svc.module_menu()
        self.svc.record_module_picks([{"module": "D.cutting_response", "why": "two kinds of control"},
                                      {"module": "F.guides_agree", "why": "guides of the leads"}])
        with mock.patch.object(AnalysisService, "run_module", return_value={"run_id": "r9"}):
            self.svc.ask_module_menu(elicit=lambda message, schema: ("accept", {"run_0": True, "note": "only this"}))
        self.svc.module_menu()  # a second menu, not answered yet
        menus = page_view.view(self.rec.state())["module_menus"]
        self.assertEqual(len(menus), 2)
        first, second = menus
        self.assertIn("D.cutting_response", first["could_run"])
        self.assertEqual(first["picks"], [{"module": "D.cutting_response", "why": "two kinds of control"},
                                          {"module": "F.guides_agree", "why": "guides of the leads"}])
        self.assertEqual((first["chosen"], first["action"], first["note"]), (["D.cutting_response"], "accept",
                                                                             "only this"))
        self.assertEqual((second["picks"], second["chosen"], second["action"]), ([], [], None))  # waiting on you
        self.assertTrue(set(first) == {"seq", "time", "could_run", "picks", "chosen", "action", "note"})

    def test_unattended_the_menu_still_runs_and_every_pick_starts_as_the_analysiss_decision(self):
        """GSE250558 on a15df12: told the user was away and that the picks went to the user, the agent skipped the
        menu 56 times. Unattended, open_project says the menu still runs, and the picks run with no form."""
        from pertura import results_report
        with mock.patch.object(AnalysisService, "unattended", new_callable=mock.PropertyMock, return_value=True):
            ok, out = self.svc.call("open_project", {"data_folder": os.path.join(self.tmp.name, "deposit")})
            self.assertNotIn("module menu", out["unattended"])  # off the default route (ADR 0047, B6)
            self.assertIn("offer_analyses runs them, as the user is away", self.svc._menu_due(self.rec.state()))
            self.svc.record_module_picks([{"module": "D.cutting_response", "why": "two kinds of control"},
                                          {"module": "F.guides_agree", "why": "guides of the leads"}])
            with mock.patch.object(AnalysisService, "run_module", return_value={"run_id": "r9"}) as started:
                out = self.svc.ask_module_menu()  # no form
            self.assertEqual(out["chosen"], ["D.cutting_response", "F.guides_agree"])
            self.assertEqual(started.call_count, 2)
        st = self.rec.state()
        answered = [m for m in st["module_menus"] if m["kind"] == "module_menu_answered"][-1]
        self.assertEqual(answered["action"], "unattended")
        decided = results_report._decided_for_the_user(st)
        self.assertTrue(any("The module menu at" in d and "D.cutting_response and F.guides_agree" in d
                            for d in decided), decided)



OVERVIEW = "A.screen_overview"
