"""Notebooks and runs (docs/adr/0024, section 6): a worker's notebook is shut when its branch closes, a run can be
stopped, long runs say how long they have run so the agent asks less often, and the session's end stops its running
jobs and records them as stopped (trial 1, attempt 3: four notebooks were never freed, a 2-hour run no one waited on
could not be stopped, and the last run vanished with the session)."""
import os
import tempfile
import unittest
from unittest import mock

from pertura.analysis_service import AnalysisError, AnalysisService


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.svc.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.svc.project)
        self.svc.design = mock.Mock(cfg={})
        self.svc.record = mock.Mock()
        self.runs = {}
        self.svc.record.state.return_value = {"runs": self.runs}

    def tearDown(self):
        self.svc.record = None
        self.svc.notebooks = {}
        self.svc.close()
        self.tmp.cleanup()


class TestNotebooks(Service):
    def test_closing_a_branch_shuts_its_workers_notebook(self):
        main, w = mock.Mock(), mock.Mock()
        self.svc.notebooks = {"main": main, "C2.states": w}
        out = self.svc.close_branch("C2.states", "done", ["run1"])
        self.assertEqual(out["notebook_shut"], ["C2.states"])
        w.shutdown.assert_called_once()
        main.shutdown.assert_not_called()
        self.assertEqual(sorted(self.svc.notebooks), ["main"])
        self.assertNotIn("notebook_shut", self.svc.close_branch("C2.states", "again", []))
        self.svc.notebooks["main"] = main
        self.assertNotIn("notebook_shut", self.svc.close_branch("main", "x", []))  # the main notebook stays

    def test_the_limits_error_says_how_to_free_a_slot(self):
        from pertura import resources as rs
        self.svc.notebooks = {"main": mock.Mock(), **{f"w{i}": mock.Mock() for i in range(rs.DEFAULTS["max_workers"])}}
        with mock.patch.object(self.svc, "_screen_dir", return_value=self.tmp.name):
            with self.assertRaises(AnalysisError) as e:
                self.svc._notebook("w9")
        self.assertIn("a thread's end", str(e.exception))  # a thread's end frees its workers (item 13; recorded, ADR 0051)
        self.assertIn("w0", str(e.exception))


class TestRuns(Service):
    def test_a_wait_with_no_run_in_flight_waits_for_the_build_or_the_measurement(self):
        """Session 7: wait_for_runs said "no run is running" while the measurement and the build ran."""
        from pertura.design_service import DesignService
        self.svc.design = mock.Mock(spec=DesignService, cfg={})
        self.svc.design.in_background.return_value = []
        with mock.patch.object(self.svc, "_need"):
            self.assertIn("neither the measurement", self.svc.wait_for_runs(minutes=0)["note"])
            build = {"what": "the screen object's build", "started": "t", "job_id": "j", "done": lambda: True}
            self.svc.design.in_background.return_value = [build]
            out = self.svc.wait_for_runs(minutes=1)
            self.assertEqual(out["ended"], [{"what": "the screen object's build", "started": "t", "job_id": "j"}])
            self.assertIn("build_screen_object", out["next"])
            measuring = {"what": "the measurement", "started": "t", "done": lambda: False}
            self.svc.design.in_background.return_value = [measuring]
            out = self.svc.wait_for_runs(minutes=0)
        self.assertEqual(out["ended"], [])
        self.assertEqual(out["still_running"], [{"what": "the measurement", "started": "t"}])
        self.assertIn("wait_for_runs again", out["next"])  # not measure over and over

    def test_a_wait_stays_under_the_clients_limit(self):
        from pertura import analysis_service as a
        self.assertLess(a.WAIT_MINUTES_MAX, 30)
        self.assertLessEqual(a.WAIT_MINUTES, a.WAIT_MINUTES_MAX)

    def test_a_run_is_stopped_and_recorded_as_stopped(self):
        self.runs["r1"] = {"run_id": "r1", "job_id": "r1", "state": "running", "what": "C.genes"}

        def sync(root):
            self.runs["r1"]["state"] = "stopped"
        self.svc.record.sync_runs.side_effect = sync
        with mock.patch("pertura.runner.stop", return_value={"stopped": True}) as stop:
            out = self.svc.stop_run("r1")
        stop.assert_called_once()
        self.assertEqual(out["state"], "stopped")
        with self.assertRaises(AnalysisError):
            self.svc.stop_run("nope")

    def test_a_running_answer_says_to_work_on_rather_than_wait(self):
        self.runs["r1"] = {"run_id": "r1", "job_id": "r1", "state": "running", "what": "C.genes"}
        with mock.patch("pertura.runner.wait"), mock.patch.object(self.svc, "_running_seconds", return_value=1800):
            out = self.svc.module_result("r1")
        self.assertEqual(out["running_seconds"], 1800)
        # a long wait is no longer proposed: past the client's limit the call's figure would not reach the model
        self.assertNotIn("wait=1800", out["next"])
        self.assertIn("wait_for_runs returns when a run ends", out["next"])
        self.assertIn("100 s", out["next"])
        self.assertIn("stop_run", out["next"])

    def test_the_first_of_several_runs_to_finish_is_given(self):
        self.runs["r1"] = {"run_id": "r1", "job_id": "r1", "state": "running", "what": "C.genes"}
        self.runs["r2"] = {"run_id": "r2", "job_id": "r2", "state": "running", "what": "B.dose"}

        def status(rid, root):
            return {"state": "done" if rid == "r2" else "running"}

        def sync(root):
            self.runs["r2"]["state"] = "done"
        self.svc.record.sync_runs.side_effect = sync
        with mock.patch("pertura.runner.status", side_effect=status), \
                mock.patch.object(self.svc, "_running_seconds", return_value=60):
            out = self.svc.module_result(run_ids=["r1", "r2"], wait=30)
        self.assertEqual(out["run_id"], "r2")
        self.assertEqual(out["still_running"], ["r1"])
        self.assertIn("r1", out["next"])

    def test_a_done_run_shows_its_figure_as_an_image(self):
        import json
        self.runs["r3"] = {"run_id": "r3", "job_id": "r3", "state": "done", "what": "C.genes"}
        folder = os.path.join(self.svc._jobs_root(), "r3")
        os.makedirs(folder)
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": "detected", "figure": "./figure.png"}, fh)
        with open(os.path.join(folder, "figure.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        out = self.svc.module_result("r3")
        self.assertTrue(out["figure_shown"])
        self.assertEqual(out["_images"][0]["mimeType"], "image/png")
        from pertura.analysis_service import RUN_FIGURE  # named as the run's own figure, which the main agent takes
        self.assertEqual(out["_images"][0]["about"], RUN_FIGURE + "r3 (C.genes)")

    def test_run_module_never_waits_long(self):
        from pertura import analysis_service as a
        self.assertLessEqual(a.RUN_WAIT_MAX, 30)
        self.assertLessEqual(a.RUN_WAIT, a.RUN_WAIT_MAX)

    def test_the_servers_end_leaves_its_jobs_running_and_shuts_its_notebooks(self):
        # a client without a window starts a server for each turn: a module outlives the turn that started it
        self.runs["r1"] = {"run_id": "r1", "job_id": "r1", "state": "running", "what": "C.genes"}
        nb = mock.Mock()
        self.svc.notebooks = {"main": nb}
        with mock.patch("pertura.runner.stop") as stop:
            self.svc.close()
        stop.assert_not_called()
        self.svc.record.sync_runs.assert_not_called()
        nb.shutdown.assert_called_once()

    def test_a_job_past_its_time_limit_is_stopped_when_the_project_opens_again(self):
        self.runs["old"] = {"run_id": "old", "job_id": "old", "state": "running", "what": "C.genes"}
        self.runs["new"] = {"run_id": "new", "job_id": "new", "state": "running", "what": "B.dose"}
        status = {"old": {"state": "orphaned", "limit_seconds": 100}, "new": {"state": "running", "limit_seconds": 100}}
        age = {"old": 400, "new": 50}
        with mock.patch("pertura.runner.status", side_effect=lambda j, root: status[j]), \
                mock.patch("pertura.runner.stop") as stop, \
                mock.patch.object(self.svc, "_running_seconds", side_effect=lambda r: age[r["run_id"]]):
            self.svc._stop_jobs_past_their_limit()
        stop.assert_called_once_with("old", self.svc._jobs_root())
        self.svc.record.sync_runs.assert_called_once()


class TestWhatTheAgentCanSee(Service):
    def test_a_recorded_figure_comes_back_as_an_image(self):
        png = os.path.join(self.tmp.name, "f.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        self.svc.record.state.return_value = {"runs": {}, "questions": {}, "findings": {}, "doubts": {},
                                              "branches": {}, "knowledge": [], "cell_sets": {}, "block_calls": {},
                                              "figures": {"fig1": {"run_id": "r", "paths": {"png": png}}}}
        with mock.patch("pertura.flow.objects", return_value={}):
            out = self.svc.read_record("figures", ["fig1"])
        self.assertEqual(out["images_shown"], 1)
        self.assertEqual(len(out["_images"]), 1)

    def test_the_figures_read_without_ids_show_the_results_own(self):
        # development run on GSE203592: the review of the figure set read the figures without ids, got no image, and
        # judged from the captions alone
        pngs = {}
        for name in ("fig1", "fig2", "first_look"):
            pngs[name] = os.path.join(self.tmp.name, f"{name}.png")
            with open(pngs[name], "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        self.svc.record.state.return_value = {
            "runs": {}, "questions": {}, "findings": {}, "doubts": {}, "branches": {}, "knowledge": [],
            "cell_sets": {}, "block_calls": {},
            "figures": {n: {"run_id": "r", "paths": {"png": p}} for n, p in pngs.items()},
            "results": {"r1": {"result_id": "r1", "figure": "fig1", "decision": "proposed", "finding_ids": []},
                        "r2": {"result_id": "r2", "figure": "fig2", "decision": "proposed", "finding_ids": []},
                        "r3": {"result_id": "r3", "figure": "first_look", "decision": "dropped", "finding_ids": []}}}
        with mock.patch("pertura.flow.objects", return_value={}):
            out = self.svc.read_record("figures")
        self.assertEqual(out["images_shown"], 2)  # the shown results' two, not the dropped one's
        self.assertEqual(set(out["figures"]), {"fig1", "fig2", "first_look"})  # the listing stays whole

    def test_the_report_is_read_by_page(self):
        from pertura import analysis_service as a
        os.makedirs(os.path.join(self.svc.project, "report"))
        text = "x" * (a.REPORT_PAGE + 10)
        with open(os.path.join(self.svc.project, "report", "report.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        self.svc.record.state.return_value = {"runs": {}}
        first = self.svc.read_record("report")["report"]
        self.assertEqual((first["page"], first["pages"], len(first["text"])), (1, 2, a.REPORT_PAGE))
        self.assertIn("ids [2]", first["next"])
        self.assertEqual(len(self.svc.read_record("report", ["2"])["report"]["text"]), 10)

    def test_a_report_without_results_says_how_results_come(self):
        os.makedirs(os.path.join(self.svc.project, "report"))
        path = os.path.join(self.svc.project, "report", "report.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# report")
        self.svc.record.state.return_value = {"runs": {}, "chosen": []}
        self.svc.record.critic.return_value = []
        self.svc.deposit = self.tmp.name
        with mock.patch("pertura.results_report.build", return_value=path), \
                mock.patch("pertura.results_report.check_links", return_value={}), \
                mock.patch.object(self.svc, "_confirmed", return_value={}), \
                mock.patch.object(self.svc, "_screen_dir", return_value=self.tmp.name):
            out = self.svc.write_report()
        self.assertIn("Propose each result in your reply", out["first"])  # the recorder records it (item 9, ADR 0051)
        self.assertNotIn("text", out)  # the reply is a summary; read_record what report gives the text
        self.assertEqual(out["pages"], 1)


class TestClosing(Service):
    def test_a_closed_question_takes_its_evidence_runs_figure(self):
        # development session 3, problem 15: every question was refused once for want of a figure ID
        self.svc.record.state.return_value = {"runs": {"r1": {}}, "figures": {"r1:figure": {"run_id": "r1"}}}
        out = self.svc.close_question("C.genes", "detected", evidence=["r1"], reason="26 of 38 targets")
        kind, event, _ = self.svc.record.append.call_args[0]
        self.assertEqual((kind, event["figure"]), ("question_closed", "r1:figure"))
        self.assertIn("r1:figure", out["figure"])


class TestShortReplies(Service):
    def test_list_modules_gives_a_line_each_and_a_contract_when_named(self):
        out = self.svc.list_modules()
        self.assertTrue(all(set(m) == {"name", "serves", "applies_when", "recipe"} for m in out["modules"]))
        self.assertLess(len(__import__("json").dumps(out)), 12000)
        one = self.svc.list_modules(name="C.genes")["module"]
        self.assertIn("fails_when", one)
        self.assertIn("def genes_recipe", one["recipe"])

    def test_a_wide_table_is_cut_and_says_how_to_narrow_it(self):
        import pandas as pd
        folder = os.path.join(self.svc._jobs_root(), "r9")
        os.makedirs(folder)
        wide = pd.DataFrame({f"c{i}": ["x" * 40] * 60 for i in range(40)})
        wide.to_csv(os.path.join(folder, "result.tsv"), sep="\t", index=False)
        self.svc.record.state.return_value = {"runs": {}}
        with mock.patch.object(self.svc, "_need"):
            out = self.svc.read_table("r9", "result", rows=40)
            self.assertLess(len(__import__("json").dumps(out)), 30000)
            self.assertIn("columns", out["note"])
            narrow = self.svc.read_table("r9", "result", rows=40, columns=["c0", "c1"])
        self.assertEqual(len(narrow["rows"]), 40)
        self.assertNotIn("note", narrow)

    def test_a_states_run_gives_within_states_both_its_tables(self):
        """Session 7 gave C.within_states a C.states_and_composition run for cell_states: as its ID alone it was 'no
        object', and as <run>:cell_states the composition in the same run was not taken."""
        import pandas as pd
        from pertura.question_modules.contract import TABLE_INPUTS
        run = "20261030T033553-C-states_and_composition-3675f0"
        folder = os.path.join(self.svc._jobs_root(), run)
        os.makedirs(folder)
        pd.DataFrame({"barcode": ["a"], "state": ["S01"], "umap_1": [0.0]}).to_csv(
            os.path.join(folder, "cell_states.tsv"), sep="\t", index=False)
        pd.DataFrame({"target": ["Hdac1"], "state": ["S04"], "mean_difference": [0.28], "q": [0.01]}).to_csv(
            os.path.join(folder, "result.tsv"), sep="\t", index=False)
        st = {"runs": {run: {"run_id": run, "job_id": run}}}
        want = {"cell_states": f"{run}:cell_states", "composition": f"{run}:result"}
        for given in ({"cell_states": run}, {"cell_states": f"{run}:cell_states"}):
            self.assertEqual(self.svc._tables_from_runs("C.within_states", dict(given), st, TABLE_INPUTS), want)
        # a composition the agent names stays its own
        mine = {"cell_states": run, "composition": "other-run:result"}
        self.assertEqual(self.svc._tables_from_runs("C.within_states", dict(mine), st, TABLE_INPUTS)["composition"],
                         "other-run:result")
        # a run without a table that has the columns gives nothing for it
        os.remove(os.path.join(folder, "result.tsv"))
        self.assertEqual(self.svc._tables_from_runs("C.within_states", {"cell_states": run}, st, TABLE_INPUTS),
                         {"cell_states": f"{run}:cell_states"})

    def test_a_table_input_named_by_no_run_comes_from_the_builds_latest_run_of_its_maker(self):
        """On 0adebaa a worker gave E.co_functional_sets no effects in three of its four calls, and C.within_states
        ran without cell_states: each failed after its job had started."""
        import pandas as pd
        from pertura.question_modules.contract import TABLE_INPUTS
        old, new = "20261109T150000-C-programs-aaaaaa", "20261109T160000-C-programs-bbbbbb"
        for run in (old, new):
            folder = os.path.join(self.svc._jobs_root(), run)
            os.makedirs(folder)
            pd.DataFrame({"target": ["A"], "score": ["P01"], "effect": [0.5]}).to_csv(
                os.path.join(folder, "result.tsv"), sep="\t", index=False)
        st = {"runs": {r: {"run_id": r, "job_id": r, "what": "module C.programs", "state": "done"}
                       for r in (old, new)}}
        filled = self.svc._tables_from_runs("E.co_functional_sets", {"program": "P01"}, st, TABLE_INPUTS)
        self.assertEqual(filled["effects"], f"{new}:result")
        given = {"effects": f"{old}:result", "program": "P01"}  # a run the call names stays its own
        self.assertEqual(self.svc._tables_from_runs("E.co_functional_sets", dict(given), st, TABLE_INPUTS), given)
        with self.assertRaisesRegex(AnalysisError, "none has ended on this build: run states first"):
            self.svc._tables_from_runs("C.within_states", {}, st, TABLE_INPUTS)

    def test_a_table_input_left_out_stays_out_unless_the_module_declares_where_it_comes_from(self):
        """A screen overview started without a common-response run had its common input filled with B.abundance's
        table, whose columns met the ones it asked for, and the overview failed on both builds of a screen."""
        import pandas as pd
        from pertura.question_modules.contract import TABLE_INPUTS, TABLES_FROM_ANOTHER_INPUT
        run = "20261031T025450-B-abundance-9475c3"
        folder = os.path.join(self.svc._jobs_root(), run)
        os.makedirs(folder)
        pd.DataFrame({"target": ["Hdac1"], "log2_vs_control_guides": [0.4], "e_distance": [1.0]}).to_csv(
            os.path.join(folder, "result.tsv"), sep="\t", index=False)
        st = {"runs": {run: {"run_id": run, "job_id": run}}}
        self.assertEqual(self.svc._tables_from_runs("A.screen_overview", {"abundance": run}, st, TABLE_INPUTS),
                         {"abundance": f"{run}:result"})
        # every module with table inputs, each given alone: an input it fills from that run is one it declares
        for module in sorted({m for (m, _) in TABLE_INPUTS}):
            keys = [k for (m, k) in TABLE_INPUTS if m == module]
            for given in keys:
                filled = self.svc._tables_from_runs(module, {given: run}, st, TABLE_INPUTS)
                for k in set(filled) - {given}:
                    self.assertIn((module, k), TABLES_FROM_ANOTHER_INPUT, f"{module} filled {k} from {given}")

    def test_where_takes_a_column_value_or_a_text_of_comparisons(self):
        """Session 7 passed where as "adj_p < 0.05" and the tool failed inside ('str' object has no attribute
        'items')."""
        import pandas as pd
        from pertura.analysis_service import rows_where
        t = pd.DataFrame({"target": ["Hdac1", "Hdac1", "Il2rb", "Dimt1"], "state": ["S04", "S10", "S04", "S10"],
                          "adj_p": [0.001, 0.2, 0.04, None]})
        self.assertEqual(list(rows_where(t, {"target": "Hdac1", "state": "S04"})["adj_p"]), [0.001])
        self.assertEqual(list(rows_where(t, {"target": ["Hdac1", "Il2rb"]})["state"]), ["S04", "S10", "S04"])
        self.assertEqual(list(rows_where(t, "adj_p < 0.05")["target"]), ["Hdac1", "Il2rb"])
        self.assertEqual(list(rows_where(t, "adj_p < 0.05 and target == 'Il2rb'")["state"]), ["S04"])
        self.assertEqual(list(rows_where(t, "state != S04")["target"]), ["Hdac1", "Dimt1"])
        for bad in ("adj_p", "q < 0.05", "target < Hdac1", 5):
            with self.assertRaises(ValueError):
                rows_where(t, bad)
        folder = os.path.join(self.svc._jobs_root(), "r8")
        os.makedirs(folder)
        t.to_csv(os.path.join(folder, "result.tsv"), sep="\t", index=False)
        self.svc.record.state.return_value = {"runs": {}}
        with mock.patch.object(self.svc, "_need"):
            out = self.svc.read_table("r8", "result", where="adj_p < 0.05")
        self.assertEqual(out["rows_total"], 2)


class TestImagesReachTheModel(unittest.TestCase):
    def test_the_server_turns_a_replys_images_into_image_content(self):
        from pertura import mcp_server
        svc = mock.Mock(TOOLS=[{"name": "t", "inputSchema": {}}], USES_ELICITATION=False, USES_CANCEL=False)
        svc.call.return_value = (True, {"a": 1, "_images": [{"data": "QUJD", "mimeType": "image/png"}]})
        out = mcp_server.handle(svc, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "t", "arguments": {}}}, {})
        content = out["result"]["content"]
        self.assertEqual([c["type"] for c in content], ["text", "image"])
        self.assertNotIn("_images", out["result"]["structuredContent"])

    def test_the_reply_names_each_image_in_their_order(self):
        from pertura import mcp_server
        svc = mock.Mock(TOOLS=[{"name": "t", "inputSchema": {}}], USES_ELICITATION=False, USES_CANCEL=False)
        svc.call.return_value = (True, {"a": 1, "_images": [
            {"data": "QUJD", "mimeType": "image/png", "about": "cell1.png"},
            {"data": "QUJD", "mimeType": "image/png", "about": "the figure of run r1 (module C.genes)"}]})
        out = mcp_server.handle(svc, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "t", "arguments": {}}}, {})
        self.assertEqual(out["result"]["structuredContent"]["images"],
                         ["cell1.png", "the figure of run r1 (module C.genes)"])
        self.assertEqual(set(out["result"]["content"][1]), {"type", "data", "mimeType"})


if __name__ == "__main__":
    unittest.main()
