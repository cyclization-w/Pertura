"""Survey first, then depth (development run on GSE267740 on 667f20a, agreed 2026-11-05): the first look ran the states
and the kidney-against-colitis contrast (448 target-programme changes), no finding read either, and the report went
straight to two results. The first look's sections are now listed by code (pertura.candidates.SECTIONS: the states map
once the states ran, the two kinds of control once compared, each other candidate structure that lists something), each
read by a finding resting on its run; a result is proposed, and the lead's complete holds, only once every section is
read; the report opens with them, and the page's view carries them."""
import json
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from test_design_service import make_deposit

from pertura import candidates, results_report
from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestTheNewLists(unittest.TestCase):
    def test_targets_shifting_state_shares_at_the_modules_level(self):
        per = pd.DataFrame({"target": ["Ccr6", "Ccr6", "Tbx21"], "state": ["S12", "S03", "S21"],
                            "state_name": ["S12 Cmss1", "S03 Uba52", "S21 Foxp3 Ikzf2"],
                            "share": [0.035, 0.2, 0.3], "control_share": [0.178, 0.21, 0.1], "q": [2e-5, 0.6, 0.01]})
        out = candidates.states_shifting(per)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["targets"][0]["states"], {"S12 Cmss1": {"share": 0.035, "control_share": 0.178}})
        self.assertEqual(candidates.states_shifting(pd.DataFrame({"target": ["A"]})), {"targets": [], "count": 0})

    def test_the_two_kinds_of_control_differ_or_not(self):
        self.assertEqual(candidates.controls_differ({"status": "detected", "diagnostics": {"genes_changed": 12}}),
                         {"differ": True, "genes_changed": 12, "count": 1})
        self.assertEqual(candidates.controls_differ({"status": "not_detected"})["count"], 0)
        self.assertEqual(candidates.controls_differ({"status": "not_enough_data"})["count"], 0)


class TestSurveyFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("cell", "contrast", "states1", "similar"):
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
        listed = {"targets_changing_between_conditions": [{"run_id": "contrast", "targets": [], "count": 448}],
                  "sets_of_targets_alike": {"run_id": "similar", "sets": [], "count": 0}}
        runs = {"C.states_and_composition": [{"run_id": "states1", "state": "done"}]}
        for name, value in (("_candidate_structures", listed), ("_module_runs", runs)):
            patcher = mock.patch.object(AnalysisService, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        return self.svc.call(tool, args)

    def find(self, fid, run_id):
        self.rec.append("finding_recorded", {"finding_id": fid, "statement": f"what {run_id} shows",
                                             "run_ids": [run_id], "kind": "description"}, rc.AGENT)

    def test_the_sections_in_their_order_each_with_the_findings_that_read_it(self):
        sections = self.svc._first_look_sections(self.rec.state())
        # every view that ran is a section, the similar targets too, whose list counts nothing (ADR 0041, step 3)
        self.assertEqual([(s["section"], s["run_id"], s["entries"]) for s in sections],
                         [("states_map", "states1", None), ("sets_alike", "similar", 0),
                          ("condition_differences", "contrast", 448)])
        self.assertEqual([s["finding_ids"] for s in sections], [[], [], []])
        self.find("f1", "states1")
        self.assertEqual(self.svc._first_look_sections(self.rec.state())[0]["finding_ids"], ["f1"])

    def test_the_states_shares_are_drawn_by_their_own_figure_not_the_map_again(self):
        """Both reports of 2026-11-08 showed the states' map twice: the shares section took its run's main figure."""
        for name in ("figure", "figure_composition"):
            self.rec.append("figure_saved", {"figure_id": f"states1:{name}", "run_id": "states1",
                                             "paths": {"png": f"/jobs/states1/{name}.png"}, "caption": name}, rc.TOOL)
        listed = {"targets_shifting_state_shares": {"run_id": "states1", "targets": [], "count": 2}}
        with mock.patch.object(AnalysisService, "_candidate_structures", return_value=listed):
            sections = {s["section"]: s["png"] for s in self.svc._first_look_sections(self.rec.state())}
        self.assertEqual(sections["state_shares"], "/jobs/states1/figure_composition.png")
        self.assertNotEqual(sections["states_map"], sections["state_shares"])  # the map: the run's main figure

    def test_only_a_finding_reads_a_section(self):
        ok, out = self.call("open_thread", about="kidney against colitis: 448 changes", raised_by="contrast",
                            observation=True, evidence={"run_id": "contrast"})
        self.assertTrue(ok, out)
        self.assertTrue(self.call("thread_status", thread_id=out["thread_id"], status="set aside",
                                  reason="the model is confounded with the experiment")[0])
        unread = [s["run_id"] for s in self.svc._first_look_unread(self.rec.state())]
        self.assertEqual(unread, ["states1", "similar", "contrast"])  # a reason to set it aside is no reading
        self.find("f1", "states1")
        self.find("f2", "contrast")
        self.find("f3", "similar")  # "nothing stands out" is a reading
        self.assertEqual(self.svc._first_look_unread(self.rec.state()), [])

    def test_the_pages_view_carries_the_sections(self):
        view = self.svc._page_state()["record"]
        self.assertEqual([s["section"] for s in view["first_look"]], ["states_map", "sets_alike", "condition_differences"])
        self.assertEqual(set(view["first_look"][0]), {"section", "label", "condition", "run_id", "png", "finding_ids"})
        self.assertEqual(view["first_look"][0]["label"], "The cells' states")  # the page shows the section's name


def test_the_report_opens_with_what_the_first_look_shows(tmp_path):
    png = tmp_path / "states.png"
    png.write_bytes(b"\x89PNG")
    st = {"findings": {"f1": {"statement": "S21 (Foxp3, Ikzf2) is Treg-like; S07 holds Tmem176a/b", "run_ids": ["s"]}},
          "runs": {"s": {}, "c": {}}, "results": {}, "figures": {}, "dossiers": {}, "doubts": {}, "threads": {},
          "questions": {}, "order": []}
    sections = [{"section": "states_map", "run_id": "s", "png": str(png), "finding_ids": ["f1"]},
                {"section": "condition_differences", "run_id": "c", "png": None, "finding_ids": []}]
    (tmp_path / "figures").mkdir()
    lines, added = results_report.first_look_lines(st, sections, str(tmp_path), 2, results_report.Cited(st))
    text = "\n".join(lines)
    assert "## First look" in text and "**The cells' states.** S21 (Foxp3, Ikzf2) is Treg-like" in text
    assert "**Effects that differ between conditions.** Not read yet" in text
    assert added == 1 and (tmp_path / "figures" / "first-look-1.png").exists() and "**Figure 2.**" in text


def test_each_section_and_each_finding_is_shown_once_by_its_statement(tmp_path):
    """GSE267740 on a15df12: the contrast between conditions ran three times and stood three times, a finding reading
    two sections was shown under both, and a finding with a check stood as its check's short line ("Tgfbr1 lowers
    Tox.") in place of its statement."""
    st = {"findings": {"f1": {"statement": "Tgfbr1 is the largest hit but rests on one guide", "target": "Tgfbr1",
                              "feature": "Tox", "direction": "down"},
                       "f2": {"statement": "No difference survives within the matched batch"}},
          "runs": {}, "results": {}, "figures": {}, "dossiers": {}, "doubts": {}, "threads": {}, "questions": {},
          "order": []}
    pngs = []
    for k in range(3):
        pngs.append(tmp_path / f"contrast{k}.png")
        pngs[-1].write_bytes(b"\x89PNG")
    sections = [{"section": "far_from_controls", "run_id": "o", "png": None, "finding_ids": ["f1"]},
                {"section": "sets_alike", "run_id": "e", "png": None, "finding_ids": ["f1"]}]
    sections += [{"section": "condition_differences", "run_id": f"c{k}", "png": str(p), "finding_ids": ["f2"]}
                 for k, p in enumerate(pngs)]
    (tmp_path / "figures").mkdir()
    lines, added = results_report.first_look_lines(st, sections, str(tmp_path), 2, results_report.Cited(st))
    text = "\n".join(lines)
    assert "Tgfbr1 is the largest hit but rests on one guide" in text and "lowers Tox" not in text
    assert text.count("Tgfbr1 is the largest hit") == 1 and "**Sets of targets alike.** Read in the findings above" in text
    assert text.count("**Effects that differ between conditions.**") == 1 and added == 1
    assert "as the first look drew it run c2" in text  # the latest contrast stands for the section


class TestWhyNoSetAlike(unittest.TestCase):
    """GSE263761 on dc5b321: the similarity run measured 5,671 target pairs with no pair of control guides holding 30
    cells to read them against, and the first look said 0 sets alike, as if nothing were alike. With no set, the
    first look gives the run's own status and summary, which tell its three outcomes apart."""

    def test_no_set_alike_carries_the_runs_own_reading(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        deposit = os.path.join(tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        svc = AnalysisService({"workspace": os.path.join(tmp.name, "ws")})
        self.addCleanup(svc.close)
        ok, out = svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        run = os.path.join(svc._jobs_root(), "sim1")
        os.makedirs(run)
        pd.DataFrame({"target": ["A", "A"], "other": ["B", "C"], "correlation": [0.6, 0.1],
                      "control_pairs_as_high": [float("nan")] * 2}).to_csv(os.path.join(run, "result.tsv"),
                                                                            sep="\t", index=False)
        said = ("2 target pairs; no pairs of control guides with 30 cells in all, pooled over the units for a "
                "reference")
        with open(os.path.join(run, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": "measured", "summary": said, "diagnostics": {"control_pairs": 0}}, fh)
        with mock.patch.object(AnalysisService, "_module_runs",
                               return_value={"E.similar_targets": [{"run_id": "sim1", "state": "done"}]}):
            alike = svc._candidate_structures(svc.record.state(), complexes=False)["sets_of_targets_alike"]
        self.assertEqual(alike["count"], 0)
        self.assertEqual(alike["why_none"], f"the run's reading (measured): {said}")
