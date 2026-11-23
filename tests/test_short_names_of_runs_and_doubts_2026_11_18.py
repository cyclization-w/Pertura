"""Problems 6 and 7 of the first run of one simple loop: the agent names runs and doubts in short ("run0047" for
"run0047-3fa9c1", "d2", "D5-D8"), and the recording took them as given, so 14 claims and 5 answers were refused. A run
named in short is the one run whose ID begins with it; a name no run (or several) answers to is dropped with a note,
and a claim left with no run takes its turn's runs. Doubts take short run-wide IDs (D1, D2, ...), and an answer names
one, its lower-case form, or a range."""
import unittest
from unittest import mock

import test_the_record_kept_by_code_adr0051 as inbox_tests
from test_results_from_findings import Service

from pertura import record as rc, recording

inbox = inbox_tests.inbox


class TestTheNames(unittest.TestCase):
    def test_a_run_named_in_short_is_the_one_run_whose_id_begins_with_it(self):
        known = {"run0047-3fa9c1", "run0048-77aa01", "run0048-9b0c2d", "20261117T223331-B-abundance-cb00a5"}
        ids, dropped = recording._runs_named(known, ["run0047", "run0048", "run9999", "20261117T223331-B-abundance-cb00a5"])
        self.assertEqual(ids, ["run0047-3fa9c1", "20261117T223331-B-abundance-cb00a5"])
        self.assertEqual(dropped, ["run0048", "run9999"])  # several answer to run0048; none to run9999

    def test_an_answer_names_a_doubt_its_lower_case_form_or_a_range(self):
        doubts = {f"D{k}": {} for k in range(1, 9)}
        self.assertEqual(recording._doubts_named(doubts, "D3"), ["D3"])
        self.assertEqual(recording._doubts_named(doubts, "d2"), ["D2"])
        self.assertEqual(recording._doubts_named(doubts, "D5-D8"), ["D5", "D6", "D7", "D8"])
        self.assertEqual(recording._doubts_named(doubts, "d5–d6"), ["D5", "D6"])
        self.assertEqual(recording._doubts_named(doubts, "D20-D22"), ["D20-D22"])  # refused by name, never dropped


class TestTheInboxTakesShortNames(Service):
    claim = inbox_tests.TestTheInbox.claim

    def setUp(self):
        super().setUp()
        parts = {"cells": None, "reference": "control_guides", "kind": "gene", "genes": None, "state_table": None,
                 "lead": None}
        for p in (mock.patch.object(self.svc, "_finding_parts", return_value=parts),
                  mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}),
                  mock.patch.object(self.svc, "_confirmed", return_value={})):
            p.start()  # as the inbox's own tests: the claims' checks need a built screen object
            self.addCleanup(p.stop)

    def test_a_claim_naming_a_run_in_short_is_recorded_on_that_run(self):
        self.svc.record.append("run_logged", {"run_id": "run0047-3fa9c1", "job_id": None, "what": "notebook cell",
                                              "code": "x", "inputs": []}, rc.TOOL)
        inbox(self.svc, "t1", {"claims": [self.claim(run_ids=["run0047"]), self.claim(run_ids=["run9999"])],
                               "turn_runs": ["r1"]})
        out = self.ok("pertura_status")
        st = self.svc.record.state()
        self.assertEqual(st["findings"]["t1-c1"]["run_ids"], ["run0047-3fa9c1"])
        self.assertEqual(st["findings"]["t1-c2"]["run_ids"], ["r1"])  # left with none: the turn's runs
        self.assertEqual(out["recorded"][0]["notes"][0]["runs_dropped"], ["run9999"])

    def test_doubts_take_short_ids_and_an_answer_may_name_a_range(self):
        inbox(self.svc, "t1", {"claims": [self.claim()]})
        self.ok("pertura_status")
        inbox(self.svc, "t1-z-doubts", {"doubts": [{"finding_id": "t1-c1", "text": f"doubt {k}",
                                                    "would_change": "a check"} for k in range(3)]})
        self.ok("pertura_status")
        self.assertEqual(sorted(self.svc.record.state()["doubts"]), ["D1", "D2", "D3"])
        inbox(self.svc, "t2", {"doubt_answers": [{"doubt_id": "d1", "outcome": "holds", "reason": "both guides"},
                                                 {"doubt_id": "D2-D3", "outcome": "holds", "reason": "the same"}]})
        self.ok("pertura_status")
        answered = {d: x["answer"]["outcome"] for d, x in self.svc.record.state()["doubts"].items() if x["answer"]}
        self.assertEqual(answered, {"D1": "holds", "D2": "holds", "D3": "holds"})


if __name__ == "__main__":
    unittest.main()
