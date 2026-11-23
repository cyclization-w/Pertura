"""The survey in the analysis service (docs/adr/0045, step 2). It runs when the user hands over the whole screen (or
Pertura runs unattended), once the cells' rule is confirmed: one run at a time, the cheaper first, each marked as the
survey's. Its runs open no section of the first look, so nothing waits for it (the gate on a0a0b8c showed how a run
started by code turns into a section a result waits on). An agent's call that the survey has already made gets the
survey's run."""
import json
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import answer, make_deposit, proposal

from pertura import design_gate, record as rc, screen_object, survey
from pertura.analysis_service import FIRST_LOOK, OVERVIEW, AnalysisService


def ready_service(root):
    """A service whose design is confirmed, whose screen object is built and whose cells' rule is confirmed."""
    deposit = os.path.join(root, "deposit")
    os.makedirs(deposit)
    make_deposit(deposit)
    svc = AnalysisService({"workspace": os.path.join(root, "ws")})
    for tool, args in (("open_project", {"data_folder": deposit}), ("measure", {}),
                       ("check_design", {"proposal": proposal()})):
        ok, out = svc.call(tool, args, elicit=lambda m, s: ("accept", {}))
        assert ok, out
    ok, out = svc.call("confirm_design", {}, elicit=answer())
    assert ok, out
    screen_object.build(deposit, design_gate.load(svc.design.design_file)[0], svc._screen_dir())
    real = svc.design.call

    def built(tool, args=None, elicit=None):
        return (True, {"job_id": "j", "state": "done", "cells": 1}) if tool == "build_screen_object" else \
            real(tool, args, elicit=elicit)
    with mock.patch.object(svc.design, "call", side_effect=built), \
            mock.patch.object(svc, "run_module", side_effect=lambda q, **k: {"run_id": f"r-{q}"}):
        ok, out = svc.call("build_screen_object", {"wait": 60}, elicit=lambda m, s: ("accept", {}))
        assert ok, out
    rule = {"value": {"filters": [{"metric": "n_genes", "min": 1}], "reasons": {"n_genes below 1": "empty"}},
            "status": "proposed", "source": "the cells overview"}
    ok, out = svc.call("check_design", {"proposal": {**proposal(), "data": {"qc": rule}}},
                       elicit=lambda m, s: ("accept", {}))
    assert ok and out["ok"], out
    with mock.patch.object(svc, "run_module", side_effect=lambda q, **k: {"run_id": f"r-{q}"}):
        ok, out = svc.call("confirm_design", {}, elicit=answer())
        assert ok, out
    return svc


class TestTheSurveyInTheService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = ready_service(self.tmp.name)
        self.rec = self.svc.record
        self.started = []

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def aim(self, scope, words):
        """The user's aim; the survey the call would start after it is left to each test (no job runs here)."""
        with mock.patch.object(self.svc, "_survey_when_due", return_value=None):
            ok, out = self.svc.call("open_thread", {"about": words, "raised_by": "the user", "user_words": words,
                                                    "scope": scope})
        self.assertTrue(ok, out)

    def log(self, module, choices=None, inputs=None, cells=None, source=None, done=False):
        """A module run as run_module logs it, without its job."""
        given = {k: v for k, v in (("cells", cells), ("choices", choices), ("inputs", inputs)) if v}
        rid = f"run{len(self.rec.state()['runs']) + 1}"
        self.rec.append("run_logged", {"run_id": rid, "job_id": rid, "what": f"module {module}" + (
            f" with {json.dumps(given)}" if given else ""), "code": "c", "inputs": [],
            **({"source": source} if source else {})}, rc.TOOL)
        if done:
            self.rec.append("run_finished", {"run_id": rid, "state": "done", "output": ""}, rc.TOOL)
        return rid

    def fake_run_module(self, module, choices=None, inputs=None, wait=0, cells=None, why=None, source=None, **k):
        self.started.append((module, source, why))
        return {"run_id": self.log(module, choices, inputs, cells, source)}

    def test_one_question_runs_no_survey(self):
        self.aim("one question", "What does the first target do?")
        self.assertFalse(self.svc._survey_on())
        self.assertIsNone(self.svc._survey_when_due())

    def test_the_whole_screen_starts_the_survey_one_run_at_a_time(self):
        self.aim("the whole screen", "Analyse the whole screen")
        self.assertTrue(self.svc._survey_on())
        expected = [c["module"] for c in self.svc._survey_comparisons() if c["module"] not in FIRST_LOOK + (OVERVIEW,)
                    and not c["not_comparable"] and not c.get("takes")]
        with mock.patch.object(self.svc, "run_module", side_effect=self.fake_run_module):
            first = self.svc._survey_when_due()
            self.assertIsNotNone(first)
            self.assertIsNone(self.svc._survey_when_due())  # one of its runs is running: the next waits
            self.rec.append("run_finished", {"run_id": first["run_id"], "state": "done", "output": ""}, rc.TOOL)
            second = self.svc._survey_when_due()
        self.assertEqual([m for m, _, _ in self.started], expected[:2])  # in the survey's order
        self.assertEqual({s for _, s, _ in self.started}, {survey.SOURCE})
        self.assertTrue(all(w.startswith("the survey: ") for _, _, w in self.started))
        self.assertNotEqual(first["comparison"], second["comparison"])

    def test_the_survey_s_runs_open_no_section_and_a_result_is_not_held(self):
        """The end-to-end check of D3: the survey's runs have ended, no finding is written, and report_result is not
        refused for the first look."""
        self.aim("the whole screen", "Analyse the whole screen")
        for module in ("C.states_and_composition", "E.similar_targets"):
            self.log(module, source=survey.SOURCE, done=True)
        self.assertEqual(self.svc._first_look_sections(self.rec.state()), [])
        with mock.patch.object(self.svc, "_survey_when_due", return_value=None):
            ok, out = self.svc.call("report_result", {"result_id": "r1", "title": "T", "finding_ids": ["f1"],
                                                      "figure": "cell"})
        self.assertNotIn("first look", str(out.get("error", "")))
        self.log("E.similar_targets", done=True)  # the agent's own run of the same view is a section, as before
        self.assertEqual([s["section"] for s in self.svc._first_look_sections(self.rec.state())], ["sets_alike"])

    def test_an_agents_call_the_survey_has_made_gets_its_run(self):
        rid = self.log("E.similar_targets", source=survey.SOURCE, done=True)
        with mock.patch.object(self.rec, "start_run", side_effect=AssertionError("a second job")), \
                mock.patch.object(self.svc, "module_result", side_effect=lambda r, w=0: {"run_id": r, "state": "done"}):
            out = self.svc.run_module("E.similar_targets", wait=0, why="the sets the overview lists")
        self.assertEqual(out["run_id"], rid)
        self.assertIn("from_the_survey", out)
        run = self.rec.state()["runs"][rid]
        # who started it stays said; the agent's why goes with it (its step and thread too, the review of d6bcc7b)
        self.assertEqual((run["source"], run["claimed"], run["why"]), (survey.SOURCE, True, "the sets the overview lists"))
        self.assertEqual([s["run_id"] for s in self.svc._first_look_sections(self.rec.state())], [rid])  # the agent's

    def finish(self, rid, status="detected"):
        """A run's job ended: its result as a module writes it, and the record told."""
        folder = os.path.join(self.svc._jobs_root(), rid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": status, "summary": f"{rid} done", "biology": [f"{rid}'s line"]}, fh)
        with open(os.path.join(folder, "result.tsv"), "w", encoding="utf-8") as fh:
            fh.write("target\teffect\nA\t1.0\n")
        self.rec.append("run_finished", {"run_id": rid, "state": "done", "output": ""}, rc.TOOL)

    def test_a_heavy_comparison_waits_while_the_agents_run_of_that_module_is_in_flight(self):
        """The agent's run of a heavy module takes that module's next turn (each runs one at a time in a workspace);
        another comparison goes meanwhile, as GPT's review of the queue asked."""
        self.aim("the whole screen", "Analyse the whole screen")
        self.log("C.genes", choices={"reference": "control_guides"})  # the agent's own, running
        with mock.patch.object(self.svc, "run_module", side_effect=self.fake_run_module):
            first = self.svc._survey_when_due()
        self.assertEqual((first["comparison"], self.started[0][0]), ("alike_opposite", "E.similar_targets"))

    def test_the_survey_starts_once_the_first_looks_code_runs_have_ended(self):
        self.aim("the whole screen", "Analyse the whole screen")
        rid = self.log("B.efficacy", source="code")
        with mock.patch.object(self.svc, "run_module", side_effect=self.fake_run_module):
            self.assertIsNone(self.svc._survey_when_due())
            self.rec.append("run_finished", {"run_id": rid, "state": "done", "output": ""}, rc.TOOL)
            self.assertIsNotNone(self.svc._survey_when_due())

    def test_rows_left_to_other_code_are_started_once_the_rest_have_ended(self):
        """B.efficacy (a modality the first look skips it for), the cells per target and the overview stayed queued
        when their own code did not start them, and the survey never completed (the review of d6bcc7b)."""
        self.aim("the whole screen", "Analyse the whole screen")
        with mock.patch.object(self.svc, "run_module", side_effect=self.fake_run_module):
            for _ in range(20):
                out = self.svc._survey_when_due()
                if out is None:
                    break
                self.finish(out["run_id"])
        modules = [m for m, _, _ in self.started]
        own = [c["module"] for c in self.svc._survey_comparisons() if c["module"] not in FIRST_LOOK + (OVERVIEW, None)]
        self.assertEqual(own[:2], ["C.genes", "E.similar_targets"])  # what the agent's first look runs too, first
        self.assertEqual(modules[:len(own)], own)
        self.assertEqual(set(modules[len(own):]), set(FIRST_LOOK[2:]) | {OVERVIEW})  # those left to other code, last
        self.assertTrue(survey.complete(self.svc._survey_rows()))

    def test_the_survey_s_runs_open_in_replies_as_their_summaries(self):
        self.aim("the whole screen", "Analyse the whole screen")
        rid = self.log("E.similar_targets", source=survey.SOURCE)
        self.finish(rid)
        full = {"run_id": rid, "state": "done", "status": "detected", "summary": "12 pairs alike",
                "biology": ["A and B"], "diagnostics": {"long": "x"}, "_images": ["img"]}
        with mock.patch.object(self.svc, "module_result", side_effect=lambda r, w=0: dict(full)), \
                mock.patch.object(self.rec, "sync_runs"):
            out = self.svc._with_ended({})
        row = out["ended_since_your_last_call"][0]
        self.assertEqual(row["result"], {"status": "detected", "summary": "12 pairs alike", "biology": ["A and B"]})
        self.assertNotIn("_images", out)
        self.assertIn("run_result gives it whole", row["survey"])

    def test_once_the_whole_screen_is_handed_over_the_survey_stays_on(self):
        self.aim("the whole screen", "Analyse the whole screen")
        self.aim("one question", "And what does A do?")  # a question of the user's: a thread of the hand-over
        self.assertTrue(self.svc._survey_on())

    def test_the_survey_ends_with_no_call_and_the_report_takes_its_last_table(self):
        """GPT's delivery test of the build: the report is written, the agent calls nothing more, the survey goes on
        to its end, and the report holds its last table; until then it says the survey still runs (D3)."""
        self.aim("the whole screen", "Analyse the whole screen")
        report = os.path.join(self.svc.project, "report", "report.md")
        with mock.patch.object(self.svc, "run_module", side_effect=self.fake_run_module), \
                mock.patch.object(self.rec, "sync_runs"):
            self.svc._survey_tick()
            self.svc._build_report()  # the agent's last call
            with open(report, encoding="utf-8") as fh:
                self.assertIn("The survey is still running", fh.read())
            for _ in range(20):  # the thread's ticks, a run ending before each
                for rid, r in list(self.rec.state()["runs"].items()):
                    if r.get("source") == survey.SOURCE and r["state"] == "running":
                        self.finish(rid)
                self.svc._survey_tick()
        self.assertTrue(survey.complete(self.svc._survey_rows()))
        with open(report, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("The survey is still running", text)
        self.assertIn("(survey/distance_overview.tsv)", text)  # the last comparison's table

    def test_the_thread_steps_the_survey_only_in_a_service_that_drives_it(self):
        import time
        self.aim("the whole screen", "Analyse the whole screen")
        self.svc._drive_the_survey()
        self.assertIsNone(self.svc._survey_driver)  # a test's service, or a single call's: no thread
        self.svc.survey_in_background = True
        with mock.patch("pertura.analysis_service.SURVEY_TICK", 0.01), \
                mock.patch.object(self.svc, "_survey_tick") as tick:
            self.svc._drive_the_survey()
            for _ in range(200):
                if tick.call_count:
                    break
                time.sleep(0.01)
            self.svc._stop_waits.set()
            self.svc._survey_driver.join(2)
        self.assertGreater(tick.call_count, 0)
        self.assertFalse(self.svc._survey_driver.is_alive())

    def test_the_status_gives_each_comparison_in_a_line(self):
        self.assertNotIn("survey", self.svc.pertura_status())  # not handed over: no survey
        self.aim("the whole screen", "Analyse the whole screen")
        rid = self.log("E.similar_targets", source=survey.SOURCE)
        lines = self.svc.pertura_status()["survey"]["comparisons"]
        self.assertIn(f"Targets alike and opposite, the screen: queued or running (running), run {rid}", lines)
        self.assertFalse(self.svc.pertura_status()["survey"]["complete"])

    def test_the_page_state_carries_the_survey_with_its_row_labels(self):
        self.assertIsNone(self.svc._page_state()["survey"])  # off until the whole screen is handed over
        self.aim("the whole screen", "Analyse the whole screen")
        page = self.svc._page_state()["survey"]
        self.assertEqual(set(page), {"facts", "rows", "complete", "ended", "waits"})  # waits: docs/adr/0047, A3
        self.assertTrue(all(r["label"] == survey.ROWS[r["row"]] for r in page["rows"]))
        json.dumps(page, default=str)  # the page state is written as JSON

    def test_the_scope_is_one_of_two_and_comes_with_the_users_words(self):
        for args in ({"scope": "everything", "user_words": "all of it"}, {"scope": "the whole screen"}):
            with mock.patch.object(self.svc, "_survey_when_due", return_value=None):
                ok, out = self.svc.call("open_thread", {"about": "a", "raised_by": "the user", **args})
            self.assertFalse(ok)
            self.assertIn("scope is what the user hands over", out["error"])
        self.aim("one question", "What does A do?")
        self.aim("the whole screen", "Now the whole screen")  # a later hand-over changes the aim
        self.assertEqual(self.rec.state()["question"]["scope"], "the whole screen")


if __name__ == "__main__":
    unittest.main()
