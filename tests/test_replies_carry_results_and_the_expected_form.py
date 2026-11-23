"""What development session 10 showed (docs/build/development_sessions/
session_10_cd4_rest_activation_gse278572_sonnet_after_session9_batch/problems_found_during_the_session.md), fixed for
any session:
- every Pertura tool loads upfront, and an object or a list sent as JSON text is read as one (problems 2 and 11);
- a run that ends comes back opened with the wait, as module_result gives it (problem 5);
- report_result names the entry marker; a figure reaches its findings' runs through the files they made, and the
  refusal says so (problems 12 and 13);
- reword_finding says "none" for a finding without checks and names its doubts still unanswered (problems 16 and 18);
- a bad cells' rule is listed once (problem 16);
- with more than two conditions, the pair to compare is the agent's (problem 9);
- the report's sample line counts hashtags that mark the conditions as conditions, not replicates, and leaves out the
  cells without a clear hashtag (problem 19); only a figure a result or finding shows has open issues (problem 20);
- the overlap advice names adjustText (problem 15); pt.save_figure is tested with the notebook's figures."""
import base64
import os
import sys
import tempfile
import types
import unittest

import numpy as np

from test_findings_doubts_and_the_report import Project
from test_the_agent_works_on_until_the_user_is_needed import LINUX, opened

from pertura import mcp_server
from pertura import record as rc
from pertura import results_report
from pertura.question_modules import figures
from pertura.question_modules.e import condition_x_perturbation, pair_needed

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5E"
                       "rkJggg==")


class Service:
    """A server's service with one tool, keeping the arguments it was called with."""
    TOOLS = [{"name": "t", "description": "d", "inputSchema": {"type": "object", "properties": {
        "changes": {"type": "object"}, "ids": {"type": "array"}, "text": {"type": "string"},
        "either": {"type": ["object", "string"]}}}}]

    def __init__(self):
        self.got = None

    def call(self, name, args, **kw):
        self.got = args
        return True, {"done": True}


def call(svc, arguments):
    return mcp_server.handle(svc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "t", "arguments": arguments}}, {})


class TestToolsAndTheirArguments(unittest.TestCase):
    def test_every_tool_loads_upfront(self):
        out = mcp_server.handle(Service(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {})
        self.assertEqual([t["_meta"] for t in out["result"]["tools"]], [{"anthropic/alwaysLoad": True}])

    def test_an_object_or_a_list_sent_as_json_text_is_read_as_one(self):
        svc = Service()
        call(svc, {"changes": '{"data.qc": {"filters": []}}', "ids": '["r1", "r2"]', "text": '{"a": 1}',
                   "either": "a: 1"})
        self.assertEqual(svc.got, {"changes": {"data.qc": {"filters": []}}, "ids": ["r1", "r2"], "text": '{"a": 1}',
                                   "either": "a: 1"})

    def test_text_that_is_not_the_declared_json_stays_for_the_tool_to_refuse(self):
        svc = Service()
        call(svc, {"changes": "data.qc: x", "ids": '{"not": "a list"}'})
        self.assertEqual(svc.got, {"changes": "data.qc: x", "ids": '{"not": "a list"}'})


class TestARunThatEndsComesBackOpened(unittest.TestCase):
    @unittest.skipUnless(LINUX, "the runner's jobs need Linux")
    def test_the_wait_gives_the_ended_runs_result_and_figure(self):
        with tempfile.TemporaryDirectory() as d:
            svc = opened(d)
            try:
                png = os.path.join(d, "f.png")
                with open(png, "wb") as fh:
                    fh.write(PNG)
                code = ("import json, shutil, time; time.sleep(1); shutil.copy(%r, 'figure.png'); json.dump("
                        "{'status': 'detected', 'summary': 'two targets act', 'figure': 'figure.png'}, "
                        "open('result.json', 'w'))" % png)
                rid = svc.record.start_run("module X", [sys.executable, "-c", code], [], svc._jobs_root())
                ok, out = svc.call("wait_for_runs", {"minutes": 2})
                self.assertTrue(ok, out)
                row = out["ended"][0]
                self.assertEqual((row["run_id"], row["summary"]), (rid, "two targets act"))
                self.assertFalse({"run_id", "state", "summary"} & set(row["result"]))  # said once, in the row
                self.assertTrue(row["result"]["figure_shown"])
                self.assertEqual(len(out["_images"]), 1)
                self.assertNotIn("next", out)  # nothing runs on
                self.assertIn(rid, svc._opened)
            finally:
                svc.close()


class TestTheRecordsWords(Project):
    def figure(self, fid, run="r1"):
        self.rec.append("figure_saved", {"figure_id": fid, "run_id": run, "paths": {"png": f"{fid}.png"},
                                         "caption": "c"}, rc.TOOL)

    def test_report_result_names_the_entry_marker(self):
        self.finding()
        self.figure("fig")
        out = self.ok("report_result", result_id="R1", title="GATA repression lowers HBZ", finding_ids=["F-GATA1"],
                      figure="fig", text="GATA repression lowers HBZ (F-GATA1). It holds in each capture "
                                         "[finding:F-GATA1].")
        self.assertEqual(out["sentences_without_an_entry"], ["GATA repression lowers HBZ (F-GATA1)."])
        self.assertIn("[finding:ID] or [run:ID]", out["entry_marker"])

    def test_a_figure_reaches_its_findings_runs_through_the_files_they_made(self):
        self.finding()
        self.rec.append("object_registered", {"object_id": "file:edist.csv", "kind": "file", "run_id": "r1",
                                              "path": "edist.csv"}, rc.TOOL)
        for rid, uses in (("r2", []), ("r3", ["file:edist.csv"])):
            self.rec.append("run_logged", {"run_id": rid, "what": "the figure", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o", "uses": uses}, rc.TOOL)
            self.figure(f"fig_{rid}", rid)
        self.assertEqual(rc.runs_read(self.rec.state(), "r3"), {"r1"})
        self.fails("report_result", "reads what they made (their files, tables or objects)", result_id="R1",
                   title="GATA repression lowers HBZ", finding_ids=["F-GATA1"], figure="fig_r2")
        self.ok("report_result", result_id="R1", title="GATA repression lowers HBZ", finding_ids=["F-GATA1"],
                figure="fig_r3")

    def test_a_rewording_without_checks_says_none_and_names_the_doubts_still_open(self):
        self.finding()
        self.ok("raise_doubt", doubt_id="D1", finding_id="F-GATA1", text="ambient RNA", would_change="x")
        out = self.ok("reword_finding", finding_id="F-GATA1", reason="clearer", statement="HBZ falls with GATA off.")
        self.assertTrue(out["checks"].startswith("none"), out["checks"])
        self.assertIn("D1", out["doubts_unanswered"])
        self.ok("answer_doubt", doubt_id="D1", outcome="weakens", reason="the haemoglobin fraction does not explain it",
                run_ids=["r1"])
        out = self.ok("reword_finding", finding_id="F-GATA1", reason="again", statement="HBZ falls when GATA is off.")
        self.assertNotIn("doubts_unanswered", out)


class TestACellsRuleRefusedOnce(unittest.TestCase):
    def test_a_bad_rule_is_listed_once(self):
        from test_design_service import make_deposit, proposal
        from pertura.design_service import DesignService, make_design_config
        with tempfile.TemporaryDirectory() as d:
            dep = os.path.join(d, "deposit")
            os.makedirs(dep)
            make_deposit(dep)
            svc = DesignService(make_design_config(dep, os.path.join(d, "state")))
            svc.measure()
            p = proposal()
            p["data"] = {"qc": {"value": {"filters": [{"measure": "nonsense", "side": "max", "cut": 1}]},
                                "status": "proposed", "source": "the cells overview"}}
            ok, out = svc.call("check_design", {"proposal": p})
            self.assertTrue(ok, out)
            self.assertEqual(len([x for x in out["problems"] if "names its metric" in x]), 1, out["problems"])


class TestThePairIsTheAgents(unittest.TestCase):
    def test_more_than_two_conditions_name_the_pairs_within_one_factor(self):
        names = ["Teff|resting", "Teff|stimulated", "Treg|resting", "Treg|stimulated"]
        said = pair_needed(names)
        self.assertIn("4 conditions", said)
        for pair in (["Teff|resting", "Teff|stimulated"], ["Teff|resting", "Treg|resting"],
                     ["Teff|stimulated", "Treg|stimulated"], ["Treg|resting", "Treg|stimulated"]):
            self.assertIn(str(pair), said)
        self.assertNotIn(str(["Teff|resting", "Treg|stimulated"]), said)
        with tempfile.TemporaryDirectory() as d:
            self.assertRaises(ValueError, condition_x_perturbation,
                              types.SimpleNamespace(condition=np.array(names * 3)), {}, d)


class TestTheOverlapAdvice(unittest.TestCase):
    def test_it_names_the_installed_package_that_moves_point_labels_apart(self):
        self.assertIn("adjust_text", figures.OVERLAP_HELP)


if __name__ == "__main__":
    unittest.main()
