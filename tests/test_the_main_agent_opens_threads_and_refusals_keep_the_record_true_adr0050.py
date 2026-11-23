"""ADR 0050, D3 and D6, from run A (GSE342764, 3bae44e).
- D3: only the main agent opens threads. The research lead opened 8 threads in 1.5 minutes, two of them waited 85
  minutes for a worker, and the main line sat for 50; a reviewer returns its view in its review.
- D6: refusals keep the record true; those that only impose an order go. Of the six refusals in A's third stage, two
  asked for what the record already held: a state finding's states run, when exactly one of its runs is one; and a
  result's figure, when its findings' runs saved one. Both are now taken from the record, and the reply says which.
  The refusals that keep the record true stay: a figure from outside its findings' runs, an unknown ID."""
import os

from test_results_from_findings import TEXT, Service

from pertura import record as rc
from pertura.analysis_service import AGENT_ARG


class TestThreadsAreTheMainAgents(Service):
    def test_a_subagent_cannot_open_a_thread_and_the_main_agent_can(self):
        ok, out = self.svc.call("open_thread", {"about": "the opposite set", "raised_by": "one level up",
                                                AGENT_ARG: "pertura:research-lead"})
        self.assertFalse(ok, out)
        self.assertIn("main agent", out["error"])
        self.assertNotIn("t1", self.svc.record.state().get("threads") or {})
        self.ok("open_thread", about="the opposite set", raised_by="one level up")


class TestRefusalsKeepTheRecordTrue(Service):
    def labelled_run(self, rid):
        self.svc.record.append("run_logged", {"run_id": rid, "what": "states", "code": "c", "inputs": []}, rc.TOOL)
        self.svc.record.append("run_finished", {"run_id": rid, "state": "ok", "output": ""}, rc.TOOL)
        folder = os.path.join(self.svc._jobs_root(), rid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "cell_states.tsv"), "w", encoding="utf-8") as fh:
            fh.write("barcode\tstate\nc1\tS1\n")

    def state_finding(self, run_ids):
        ok, out = self.svc.call("record_finding", {"finding_id": "f9", "targets": ["GATA1"], "feature": "S1",
                                                   "direction": "up", "kind": "state", "run_ids": run_ids,
                                                   "statement": "GATA1-i fills S1."})
        return out.get("error", "")

    def test_a_state_finding_takes_its_one_states_run(self):
        self.labelled_run("s1")
        error = self.state_finding(["s1", "r1"])
        self.assertNotIn("needs state_run", error)  # taken from its runs; what the fixture lacks is the screen
        self.labelled_run("s2")
        self.assertIn("needs state_run", self.state_finding(["s1", "s2"]))  # two: which one is the agent's to say

    def test_a_results_figure_is_its_findings_latest_when_none_is_named(self):
        out = self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                      text=TEXT)
        self.assertEqual(self.svc.record.state()["results"]["res1"]["figure"], "fig1")
        self.assertIn("fig1", out["figure_taken"])
        error = self.refused("report_result", result_id="res2", title="A second", finding_ids=["f2"], text=TEXT)
        self.assertIn("none of its findings' runs saved a figure", error)

    def test_the_refusals_that_keep_the_record_true_stay(self):
        self.assertIn("drawn from the runs of its findings", self.refused(
            "report_result", result_id="res1", title="t", finding_ids=["f1"], figure="r2", text=TEXT))
        self.assertIn("no result, finding or thread", self.refused(
            "report_result", result_id="res1", title="t", finding_ids=["f1"], figure="fig1", text=TEXT,
            raised_by="r9"))
