"""ADR 0038: typed contracts at the record's doors (pydantic), from the review of D on a9411ca.
- D1: the four tools' schemas come from their contracts (no titles, no $defs), each call is checked before it runs,
  an error names the field and what it takes, and a thread given as its object is read by its ID (the lead's
  review crashed on one);
- D2: a finding states a claim that code checks, or says it is a description (the checks ran on 1 of 9 findings in
  two sessions);
- D4: a thread ends with its evidence (a worker's score was ended in a line of reason and the report lost it), and
  the lead's complete holds only when each of its threads has ended or been followed into a finding;
- D5: a result proposed again with nothing changed is the one there."""
import json
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import contracts
from pertura import record as rc
from pertura.analysis_service import TOOLS, AnalysisService, folded_arguments


class TestTheSchemas(unittest.TestCase):
    def test_the_four_tools_take_their_schemas_from_their_contracts(self):
        tools = {t["name"]: t for t in TOOLS}
        for name, model in contracts.MODELS.items():
            schema = tools[name]["inputSchema"]
            self.assertEqual(schema, contracts.schema(model))
            text = json.dumps(schema)
            self.assertNotIn("$defs", text)
            self.assertNotIn("$ref", text)
            self.assertNotIn('"title": "', text.replace('"title": {', ""))  # a property named title is no title
        self.assertEqual(tools["thread_status"]["inputSchema"]["properties"]["status"]["enum"],
                         list(rc.THREAD_STATUSES))

    def test_an_optional_value_folds_as_a_plain_one_does(self):
        self.assertEqual(folded_arguments("record_finding", {"direction": "Up", "kind": "Gene_Set"}),
                         {"direction": "up", "kind": "gene_set"})
        self.assertEqual(folded_arguments("thread_status", {"status": "set_aside"}), {"status": "set aside"})


class TestTheChecks(unittest.TestCase):
    def test_a_thread_given_as_its_object_is_read_by_its_id(self):
        # shown first with the research lead's review (retired 2026-11-20); the same Id reads a thread's end
        args, wrong = contracts.checked("thread_status", {"thread_id": {"thread_id": "t12"}, "status": "explained",
                                                          "reason": "r", "finding_ids": [{"finding_id": "F1"}, "F2"]})
        self.assertIsNone(wrong)
        self.assertEqual((args["thread_id"], args["finding_ids"]), ("t12", ["F1", "F2"]))

    def test_an_error_names_the_field_and_what_it_takes(self):
        _, wrong = contracts.checked("record_finding", {"finding_id": "F1", "statement": "s", "run_ids": ["r1"],
                                                        "target": "A", "feature": "g", "direction": "up",
                                                        "kind": "gene", "reference": "controls", "strict": True})
        self.assertIn("reference is one of 'control_guides', 'other_targets' or 'no_guide'", wrong)
        self.assertIn("strict: not an argument of this tool, which takes finding_id", wrong)

    def test_a_finding_states_a_claim_or_says_it_is_a_description(self):
        base = {"finding_id": "F1", "statement": "s", "run_ids": ["r1"]}
        _, wrong = contracts.checked("record_finding", base)
        self.assertIn("kind: description", wrong)
        self.assertEqual(contracts.checked("record_finding", {**base, "kind": "description"})[1], None)
        self.assertEqual(contracts.checked("record_finding", {**base, "kind": "description", "target": "A"})[1],
                         None)  # a target named, no claim: a description still
        _, wrong = contracts.checked("record_finding", {**base, "target": "A", "feature": "g", "direction": "up"})
        self.assertIn("names its kind", wrong)
        claim = {**base, "target": "A", "feature": "g", "direction": "up", "kind": "gene"}
        self.assertEqual(contracts.checked("record_finding", claim)[1], None)
        claim.pop("statement")  # a claim without its sentence, as before the contracts
        self.assertEqual(contracts.checked("record_finding", claim)[1], None)
        several = {**base, "targets": ["A", "B"], "feature": "table:score", "direction": "up", "kind": "score"}
        self.assertEqual(contracts.checked("record_finding", several)[1], None)
        self.assertIn("not both", contracts.checked("record_finding", {**several, "target": "A"})[1])
        self.assertIn("each target once", contracts.checked("record_finding", {**several, "targets": ["A", "A"]})[1])
        self.assertIn("targets: List should have at most 6", contracts.checked(
            "record_finding", {**several, "targets": list("ABCDEFG")})[1])
        _, wrong = contracts.checked("record_finding", {"finding_id": "F1", "run_ids": [], "kind": "description"})
        self.assertIn("in a sentence (statement)", wrong)  # a description says itself; no runs is the record's call

    def test_a_thread_ends_with_its_reason_and_needs_no_run_named(self):
        """GSE190604 (a9510b4): a refusal asking for runs was met with runs from before the thread; the coverage now
        says when a thread ended with no run of its own (record.ran_since_opened)."""
        _, wrong = contracts.checked("thread_status", {"thread_id": "t2", "status": "out of data", "reason": "r"})
        self.assertFalse(wrong)
        _, wrong = contracts.checked("thread_status", {"thread_id": "t2", "status": "set aside", "reason": " "})
        self.assertIn("reason", wrong)


class TestThroughTheService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("r1", "r2"):
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
        ok, out = self.svc.call("record_finding", {"finding_id": "F0", "statement": "Two blocks of targets [run:r1].",
                                                   "run_ids": ["r1"], "kind": "description"})
        assert ok, out
        self.rec.append("thread_opened", {"thread_id": "t2", "about": "P2: a score from the contrast",
                                          "raised_by": "one level up", "joins": ["F0"]}, rc.AGENT)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        return self.svc.call(tool, args)

    def test_an_explained_thread_needs_no_finding_and_the_coverage_says_it_had_no_run(self):
        """GSE190604 (a9510b4): t9 ended 15 s after it opened, on a description written to pass this refusal."""
        ok, out = self.call("thread_status", thread_id="t2", status="explained", reason="the score is done")
        self.assertTrue(ok, out)
        self.assertFalse(rc.ran_since_opened(self.rec.state(), "t2"))
        self.rec.append("run_logged", {"run_id": "r9", "what": "t2's own", "code": "c", "inputs": [],
                                       "thread_id": "t2"}, rc.TOOL)
        self.assertTrue(rc.ran_since_opened(self.rec.state(), "t2"))
        ok, out = self.call("record_finding", finding_id="F1", statement="The score falls [run:r1].",
                            run_ids=["r1"], thread_id="t2", kind="description")
        self.assertTrue(ok, out)
        ok, out = self.call("thread_status", thread_id="t2", status="explained", reason="the score is done")
        self.assertTrue(ok, out)  # by default, the findings recorded with its thread_id
        self.assertEqual(self.rec.state()["threads"]["t2"]["status"], "explained")
        self.rec.append("thread_opened", {"thread_id": "t3", "about": "the blocks", "raised_by": "F0"}, rc.AGENT)
        ok, out = self.call("thread_status", thread_id="t3", status="explained", reason="F0 holds it",
                            finding_ids=["F0"])
        self.assertTrue(ok, out)
        self.assertEqual(self.rec.state()["threads"]["t3"]["findings"], ["F0"])  # the findings it names join it

    def test_a_result_proposed_again_unchanged_is_the_one_there(self):
        ok, out = self.call("record_finding", finding_id="F1", statement="The score falls [run:r1].",
                            run_ids=["r1"], thread_id="t2", kind="description")
        self.assertTrue(ok, out)
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        args = dict(result_id="R1", title="The score falls", finding_ids=["F1"], figure="fig1",
                    text="The score falls [finding:F1].")
        ok, first = self.call("report_result", **args)
        self.assertTrue(ok, first)
        ok, again = self.call("report_result", **args)
        self.assertTrue(ok, again)
        self.assertTrue(again["unchanged"])
        self.assertEqual(self.rec.state()["results"]["R1"]["version"], first["version"])


if __name__ == "__main__":
    unittest.main()
