"""The record as it stands (2026-11-16): the one view the research lead chooses its next step from, read from the
record's fields and never from sentences. It replaced the coverage's lists of what must be done, which invited
re-checking one claim. And an "enough" from the user is superseded by a later reply or a thread opened after it, as
a complete is by a thread opened after it."""
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestTheRecordAsItStands(unittest.TestCase):
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

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def finding(self, fid, **fields):
        self.rec.append("finding_recorded", {"finding_id": fid, "run_ids": ["r1"], "statement": f"{fid}.", **fields},
                        rc.AGENT)

    def checks(self, fid, **outcomes):
        for check, outcome in outcomes.items():
            self.rec.append("check_recorded", {"finding_id": fid, "check": check, "outcome": outcome,
                                               **({"reason": "one unit"} if outcome == "not run" else {})}, rc.TOOL)

    def as_it_stands(self):
        ok, out = self.svc.call("read_record", {"what": "coverage"})
        self.assertTrue(ok, out)
        return out["coverage"]

    def test_findings_by_their_checks_with_the_checks_that_did_not_run(self):
        self.finding("f1", target="MED12", feature="KLF2", direction="down", kind="gene")
        self.checks("f1", lead="agrees", guides_agree="agree", calibration="beyond the control groups",
                    half="not run")
        self.finding("f2", target="MED12", feature="MYC", direction="down", kind="gene")  # past no control line
        self.checks("f2", lead="agrees", calibration="within the control groups' range")
        self.finding("f3", target="SOCS3", feature="FABP5", direction="up", kind="gene")
        self.checks("f3", lead="agrees", guides_agree="disagree", calibration="beyond the control groups")
        self.finding("f4", kind="description")
        self.finding("f5", target="IL2RA", feature="S03", direction="up", kind="state")  # no check yet
        cov = self.as_it_stands()
        rec = cov["the_record_as_it_stands"]
        self.assertEqual(rec["findings_their_checks_confirm"],
                         [{"finding_id": "f1", "targets": ["MED12"], "feature": "KLF2", "direction": "down",
                           "kind": "gene", "checks_not_run": ["half"]}])
        self.assertEqual([x["finding_id"] for x in rec["findings_their_checks_contradict"]], ["f3"])
        self.assertEqual([x["finding_id"] for x in rec["findings_not_settled_yet"]], ["f2", "f5"])
        self.assertEqual([x["finding_id"] for x in rec["descriptions_no_check_reads"]], ["f4"])
        self.assertEqual(rec["shared"]["targets_in_several_findings"], {"MED12": ["f1", "f2"]})
        for gone in ("complete_waits_on", "first_look_unread", "findings_no_result_cites", "steps_of_the_threads",
                     "findings_without_a_figure", "structures", "candidate_structures"):
            self.assertNotIn(gone, cov)

    def test_a_gene_that_is_itself_a_target_is_shared(self):
        self.svc._screen_labels = lambda: {"MED12", "KLF2", "IL2RA"}
        self.finding("f1", target="MED12", feature="KLF2", direction="down", kind="gene")
        self.finding("f2", target="KLF2", feature="S04", direction="down", kind="state")
        rec = self.as_it_stands()["the_record_as_it_stands"]
        self.assertEqual(rec["shared"]["genes_that_are_targets_too"],
                         {"KLF2": {"findings_on_it": ["f1"], "its_own_findings": ["f2"]}})

    def test_enough_gives_way_to_a_later_reply_or_thread_not_to_the_analysis_own(self):
        self.ok_reply("enough", "That's enough.")
        self.assertTrue(rc.enough_stands(self.rec.state()))
        self.rec.append("reply_recorded", {"kind": "knowledge", "words": "noted", "by_analysis": True}, rc.AGENT)
        self.assertTrue(rc.enough_stands(self.rec.state()))  # the analysis's own note does not reopen it
        self.finding("f1", target="MED12", feature="KLF2", direction="down", kind="gene")
        self.rec.append("reply_recorded", {"kind": "deepen", "words": "Go further with MED12.", "finding_id": "f1"},
                        rc.AGENT)
        self.assertFalse(rc.enough_stands(self.rec.state()))
        self.assertIsNotNone(self.rec.state().get("enough"))  # kept for the report's end line
        self.ok_reply("enough", "Now enough.")
        self.assertTrue(rc.enough_stands(self.rec.state()))
        self.rec.append("thread_opened", {"thread_id": "t9", "about": "one more", "raised_by": "the user"}, rc.AGENT)
        self.assertFalse(rc.enough_stands(self.rec.state()))

    def ok_reply(self, kind, words):
        self.rec.append("reply_recorded", {"kind": kind, "words": words}, rc.AGENT)


if __name__ == "__main__":
    unittest.main()
