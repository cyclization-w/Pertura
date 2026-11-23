"""Revision 3 of the field sources (docs/build/the_fields_of_the_retired_record_tools_and_where_each_comes_from_
2026-11-17.md). Trial 2 lost 28 of 82 claims, 6 of 7 results and 20 of 36 doubts to refusals. Now code fills what the
runs hold, a claim whose checks cannot start is kept as a description with what it needs, a result without a saved
figure is kept with a run standing for it, and a claim's thread, the user's words and the result chain come from code
or fixed lines. No main line, no forks: a picked step is the main agent's own."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from test_results_from_findings import Service
from test_the_record_kept_by_code_adr0051 import inbox

from pertura import analysis_service as asv, record as rc, recording

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import recorder  # noqa: E402


class Inbox(Service):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}),
                  mock.patch.object(self.svc, "_confirmed", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def apply(self, tag, data, agent="main"):
        inbox(self.svc, tag, {"range": {"session": "s", "agent": agent, "first": tag + "a", "last": tag + "b"}, **data})
        return self.ok("pertura_status")["recorded"][0]

    @staticmethod
    def claim(**more):
        return {"statement": "GATA1 knockdown raises SPI1 [run:r1]", "targets": ["GATA1"],
                "feature": {"kind": "gene", "name": "SPI1"}, "direction": "up", "run_ids": ["r1"], **more}


class TestAClaimIsNeverRefusedWhole(Inbox):
    def test_a_claim_whose_checks_cannot_start_is_kept_as_a_description_with_what_it_needs(self):
        out = self.apply("t1", {"claims": [self.claim(feature={"kind": "gene_set", "name": "DAM"})]})
        applied = out["applied"][0]
        self.assertTrue(applied["checks"].startswith("not started: "))
        self.assertIn("name its genes: Claim t1-c1: targets GATA1; feature gene_set DAM; direction up; genes",
                      applied["to_check_it"])
        f = self.svc.record.state()["findings"]["t1-c1"]
        self.assertEqual((f["target"], f["kind"], f["run_ids"]), ("GATA1", None, ["r1"]))
        self.assertEqual(out["claims"], {"found": 1, "with_checks": 0, "described": 1, "refused": 0})

    def test_a_score_claim_takes_the_table_its_runs_saved(self):
        self.svc.record.append("run_logged", {"run_id": "r3", "what": "a cell", "inputs": [],
                                              "code": "pt.save_table('dam_up', t)\npt.save_table(\"other\", u)"},
                               rc.TOOL)
        st = self.svc.record.state()
        self.assertEqual(recording._score_table_for(self.svc, st, "DAM up", ["r3"]), "table:dam_up")
        self.assertIsNone(recording._score_table_for(self.svc, st, "IFN", ["r3"]))  # two saved, none named so
        self.assertEqual(recording._score_table_for(self.svc, st, "table:x", ["r3"]), "table:x")

    def test_a_state_claim_takes_the_one_labelling_that_holds_its_state(self):
        folder = tempfile.mkdtemp(dir=self.tmp.name)
        tables = {}
        for rid, states in (("ra", "S01,S05"), ("rb", "S01,S02"), ("rc", "S05,S07")):
            tables[rid] = os.path.join(folder, f"{rid}.tsv")
            with open(tables[rid], "w", encoding="utf-8") as fh:
                fh.write("barcode\tstate\n" + "".join(f"c{i}\t{s}\n" for i, s in enumerate(states.split(","))))
            self.svc.record.append("run_logged", {"run_id": rid, "what": "states", "code": "c", "inputs": []}, rc.TOOL)
        st = self.svc.record.state()
        with mock.patch.object(self.svc, "_run_labelling", side_effect=lambda r: tables.get(r)):
            self.assertEqual(recording._labelling_for(self.svc, st, "S05", ["ra", "rb"]), "ra")
            self.assertIsNone(recording._labelling_for(self.svc, st, "S05", ["ra", "rc"]))  # two could be meant
            self.assertIsNone(recording._labelling_for(self.svc, st, "S05", ["r1"]))  # three in the project
        with mock.patch.object(self.svc, "_run_labelling", side_effect=lambda r: tables["ra"] if r == "ra" else None):
            self.assertEqual(recording._labelling_for(self.svc, st, "S05", ["r1"]), "ra")  # the project's only one
            self.assertIsNone(recording._labelling_for(self.svc, st, "S09", ["r1"]))  # not a label in it


class TestAResultIsNeverRefusedForWantOfAFigure(Inbox):
    def test_its_latest_run_stands_for_its_figure_until_one_is_saved(self):
        out = self.apply("t1", {"results": [{"title": "The second finding holds", "text": "It holds [run:r2].",
                                             "finding_ids": ["f2"]}]})
        self.assertIn("none saved yet", out["applied"][0]["figure"])
        r = self.svc.record.state()["results"]["r1"]
        self.assertEqual((r["figure_run"], r["version"]), ("r2", 1))
        self.svc.record.append("figure_saved", {"figure_id": "fig2", "run_id": "r2", "paths": {"png": "fig2.png"},
                                                "caption": "the second"}, rc.TOOL)
        self.ok("pertura_status")
        r = self.svc.record.state()["results"]["r1"]
        self.assertEqual((r["figure"], r["version"]), ("fig2", 2))


class TestTheResultChain(Inbox):
    def first(self):
        self.apply("t1", {"results": [{"title": "GATA1 knockdown raises SPI1", "text": "It does [run:r1].",
                                       "finding_ids": ["f1"],
                                       "next_steps": [{"question": "Does it hold in each guide?",
                                                       "move": "reproducibility", "why": "one guide could carry it",
                                                       "recommended": True}]}]})

    def test_a_from_line_links_a_result_to_the_one_that_raised_its_question(self):
        self.first()
        out = self.apply("t2", {"results": [{"title": "It holds in each guide", "text": "Both [run:r2].",
                                             "finding_ids": ["f2"]}],
                                "from_lines": [{"raised_by": "r1", "answers": "Does it hold in each guide?"}]})
        r = self.svc.record.state()["results"]["r2"]
        self.assertEqual((r["raised_by"], r["answers"]), ("r1", "Does it hold in each guide?"))
        self.assertNotIn("link_dropped", out["applied"][0])

    def test_a_link_to_no_earlier_result_is_dropped_and_the_result_stays(self):
        out = self.apply("t1", {"results": [{"title": "One", "text": "It does [run:r1].", "finding_ids": ["f1"],
                                             "raised_by": "r9"}]})
        self.assertIn("link_dropped", out["applied"][0])
        self.assertIsNone(self.svc.record.state()["results"]["r1"].get("raised_by"))

    def test_the_first_result_after_the_users_pick_follows_the_result_picked_from(self):
        self.first()
        self.apply("t2", {"user_decisions": [{"kind": "next_step", "words": "option 1: go on", "message_uuid": "m1",
                                              "refers_to": "1"}],
                          "user_messages": {"m1": "option 1: go on"}})
        st = self.svc.record.state()
        picked = [t for t, th in st["threads"].items() if th.get("next_of") == "r1"]
        self.assertEqual(len(picked), 1)
        self.assertNotIn(picked[0], self.svc._threads_waiting(st))  # no main line: the picked step is the agent's
        out = self.apply("t3", {"claims": [self.claim(run_ids=["r2"])],
                                "results": [{"title": "It holds", "text": "Yes [run:r2].", "claims": [0]}]})
        st = self.svc.record.state()
        self.assertEqual(st["findings"]["t3-c1"]["thread_id"], picked[0])
        self.assertEqual((st["results"]["r2"]["raised_by"], st["results"]["r2"]["answers"]),
                         ("r1", "Does it hold in each guide?"))
        self.assertIn("result r2", [a["what"] for a in out["applied"]])
        text = rc.result_chain_text(st)
        self.assertIn("\n  r2 [proposed, version 1] (from r1): It holds", text)  # under r1, indented

    def test_before_the_design_is_confirmed_the_users_words_answer_the_design(self):
        self.first()
        with mock.patch.object(self.svc, "_confirmed", side_effect=asv.AnalysisError("not confirmed")):
            out = self.apply("t2", {"user_decisions": [{"kind": "keep", "words": "keep r1", "message_uuid": "m1",
                                                        "refers_to": "r1"}],
                                    "user_messages": {"m1": "keep r1"}})
        self.assertIn("a design answer", out["refused"][0]["why"])


class TestAClaimsThread(Inbox):
    def test_from_its_runs_else_the_step(self):
        # an observation's thread (the research lead's, raised one level up, was the case until it was retired)
        tid = self.svc.open_thread(about="probe the states", raised_by="r1", observation=True)["thread_id"]
        self.svc.record.append("run_logged", {"run_id": "r5", "what": "a cell", "code": "c", "inputs": [],
                                              "worker": f"{tid}-probe"}, rc.TOOL)
        st = self.svc.record.state()
        self.assertEqual(recording._thread_of(st, ["r5"]), tid)
        self.assertEqual(recording._thread_of(st, ["r1"], step=(tid, "r1")), tid)
        self.assertIsNone(recording._thread_of(st, ["r1"]))
        self.assertIn(tid, self.svc._threads_waiting(st))  # an observation waits for a worker


class TestTheRecordersFixedLines(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"PERTURA_STATUS_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)

    def test_a_claim_line_gives_what_its_checks_need(self):
        text = ("AGENT: Claim c7: targets ARID2; feature state S09; direction up; labelling run0012-abcdef\n"
                "AGENT: Claim c8: targets ZNF644; feature score DAM; direction up; table dam_up\n"
                "AGENT: Claim c9: targets A, B; feature gene_set IFN; direction down; genes ISG15, MX1; cells model=iMG")
        c7, c8, c9 = recorder.fixed_forms(text, {})["claims"]
        self.assertEqual(c7["feature"]["labelling"], "run0012-abcdef")
        self.assertEqual(c8["feature"]["name"], "table:dam_up")
        self.assertEqual((c9["feature"]["genes"], c9["cells"]), (["ISG15", "MX1"], {"model": "iMG"}))
        for c in (c7, c8, c9):  # what the recorder sends must pass the schema
            recording.Claim.model_validate(c)

    def test_a_from_line_is_read_and_prose_is_not(self):
        lines = recorder.fixed_forms("AGENT: **From R1:** Does it hold in each guide?\n"
                                     "AGENT: From these results: nothing", {})["from_lines"]
        self.assertEqual(lines, [{"raised_by": "r1", "answers": "Does it hold in each guide?"}])

    def test_only_the_prompts_the_user_sent_are_their_words(self):
        recorder.record_prompt({"session_id": "s1", "prompt": "Which regulators control DAM?"})
        typed = set(json.load(open(os.path.join(self.home, "recorder", "s1.user_prompts.json"), encoding="utf-8")))
        entries = [{"uuid": "u1", "type": "user", "message": {"content": "Which regulators control DAM?"}},
                   {"uuid": "u2", "type": "user", "message": {"content": "Thread t3: probe the states"}}]
        text, users, _, _ = recorder.turn_of(entries, typed=typed)
        self.assertEqual(list(users), ["u1"])
        self.assertIn("NOTE (not the user's words): Thread t3", text)


if __name__ == "__main__":
    unittest.main()
