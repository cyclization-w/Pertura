"""Item 13 of docs/build_goal_session6_fixes.md (ADR 0035, D6): the subtraction. Sessions 4 to 6 never called 12 of
the analysis server's 46 tools, and session 6's report took 53 record calls, 11 of which failed. What the agent sees
is now the tools that serve the design, the analysis and the report; the others are gone from the tool list, the
skill and the replies, their code kept:
- the shown tools, and each retired tool with the tool that does its work;
- the server refuses a retired tool by name; the service still runs it, so older records and tests read;
- the thread tools take over the branches (a thread of alternatives gives the workers' briefs and, when it ends, the
  table of every way) and the user's question (open_thread with the user's words);
- a finding carries what is known beside it and how it differs (prior, differs), and its checks start again from
  reword_finding (check_again);
- a figure review answers the latest review's issues (answers);
- pertura_status gives the digest: what waits on the user, the thread the agent is on, what happened since, by
  thread, and the open threads as options;
- no shown description, the server's instructions or the skill names a retired tool."""
import glob
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import make_deposit

from pertura import analysis_service as asv
from pertura import digest, kinds, mcp_server
from pertura import record as rc
from pertura.analysis_service import AnalysisService

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin")
KEPT = {"pertura_status", "open_project", "describe_deposit", "read_metadata", "measure", "check_design",
        "confirm_design", "get_design", "build_screen_object", "run_code", "run_result",
        "wait_for_runs", "read_table", "gene_sets", "read_knowledge", "read_record",
        "stop_run", "search_literature", "prior_screens", "write_report"} | set(kinds.KINDS)
# the record tools left every agent's list (docs/adr/0051, D1): the recorder records from the replies, and the inbox
# applies it through their methods
# the analyses by the field's kinds (ADR 0044). Retired before the freeze (2026-11-16), never used in four development
# runs: write_plan, the module picker's next_analyses, record_module_picks and offer_analyses, the advisor's
# record_advice


class TestTheList(unittest.TestCase):
    def test_the_shown_tools_and_where_each_retired_ones_work_went(self):
        shown = {t["name"] for t in AnalysisService.TOOLS}
        self.assertEqual(shown - {"report_result"}, KEPT)
        self.assertFalse(shown & set(asv.RETIRED))
        self.assertTrue(set(asv.RETIRED) <= asv.TOOL_NAMES)  # the code stays
        for name, instead in asv.RETIRED.items():
            named = [t for t in shown if t in instead]
            self.assertTrue(named or "design" in instead, (name, instead))

    def test_no_shown_text_names_a_retired_tool(self):
        texts = [asv.INSTRUCTIONS] + [t["description"] for t in AnalysisService.TOOLS]
        for pattern in ("skills/pertura/*.md", "skills/pertura-knowledge/**/*.md", "agents/*.md",
                        "output-styles/*.md", "commands/*.md"):
            for path in glob.glob(os.path.join(PLUGIN, pattern), recursive=True):
                with open(path, encoding="utf-8") as fh:
                    texts.append(f"{os.path.basename(path)}: {fh.read()}")
        for name in asv.RETIRED:
            said = [t[:60] for t in texts if f"{name}(" in t or f"`{name}`" in t or f" {name} " in t
                    or f" {name}," in t or f" {name}." in t or f"({name}" in t]
            self.assertEqual(said, [], name)


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.svc.record.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        self.svc.record.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out


class TestTheServer(Service):
    def test_a_retired_tool_is_refused_by_name_and_still_runs_in_the_service(self):
        listed = mcp_server.handle(self.svc, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {})
        self.assertNotIn("add_question", {t["name"] for t in listed["result"]["tools"]})
        out = mcp_server.handle(self.svc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                           "params": {"name": "record_check", "arguments": {}}}, {})
        self.assertIn("error", out)
        self.assertTrue(self.svc.call("raise_doubt", {"doubt_id": "d1", "text": "t", "would_change": "w"})[0])


class TestThreadsTakeOverTheBranches(Service):
    def test_two_channels_test_a_reading_and_open_thread_offers_no_alternatives(self):
        """B3b: a doubt asks whether a finding holds, a fork where another way leads; the
        alternatives path, unused in the development runs, left open_thread."""
        from pertura import analysis_service
        tool = next(t for t in analysis_service.TOOLS if t["name"] == "open_thread")  # the inbox's (docs/adr/0051)
        offered = set(tool["inputSchema"]["properties"])
        self.assertTrue({"doubt_id", "fork_of", "choice"} <= offered)
        self.assertFalse({"alternatives", "base", "measures"} & offered)
        with self.assertRaises(AssertionError):
            self.ok("open_thread", about="the reference", raised_by="r1", alternatives=[{"name": "x", "change": "y"}])

    def test_a_thread_on_a_doubt_and_one_the_user_raised(self):
        self.ok("raise_doubt", doubt_id="d1", text="one unit carries it", would_change="it fades without unit 2")
        self.ok("open_thread", about="is it one unit?", raised_by="d1", thread_id="u", doubt_id="d1")
        self.assertEqual(self.svc.record.state()["branches"]["u"]["doubt_id"], "d1")
        self.ok("open_thread", about="the user asks what GATA1 does", raised_by="the user",
                user_words="what does GATA1 do here?")
        self.ok("open_thread", about="and the pairs", raised_by="the user", user_words="and the pairs?")
        st = self.svc.record.state()
        self.assertEqual(st["question"]["text"], "what does GATA1 do here?")  # the first is the question
        self.assertTrue(st["question"]["relayed"])
        self.assertEqual([t["user_words"] for t in st["threads"].values() if t.get("user_words")],
                         ["what does GATA1 do here?", "and the pairs?"])


class TestBesideAFinding(Service):
    def test_prior_knowledge_and_a_difference_are_kept_with_the_finding(self):
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1", run_ids=["r1"],
                prior={"text": "GATA1 represses PU.1", "source": "PMID 10086723"}, kind="description")
        st = self.svc.record.state()
        self.assertEqual(st["knowledge"][-1]["finding_id"], "f1")
        self.assertEqual(st["knowledge"][-1]["tag"], rc.PRIOR)
        with self.assertRaises(AssertionError):
            self.ok("reword_finding", finding_id="f1", reason="r", differs={"text": "x", "source": "a blog"})
        self.ok("reword_finding", finding_id="f1", reason="the paper says otherwise",
                differs={"text": "the paper saw no change", "source": "the paper"})
        self.assertEqual(len(self.svc.record.state()["findings"]["f1"]["wordings"]), 0)  # nothing reworded

    def test_check_again_starts_the_checks(self):
        self.ok("record_finding", finding_id="f2", statement="s", run_ids=["r1"], kind="description")
        with mock.patch.object(self.svc, "check_finding", return_value={"checks_run": "c9"}) as again:
            out = self.ok("reword_finding", finding_id="f2", reason="their run failed", check_again=True)
        again.assert_called_once_with("f2")
        self.assertEqual((out["checks_run"], out["checks"]), ("c9", "started again"))


class TestTheDigest(Service):
    def test_what_waits_the_thread_taken_what_happened_since_and_the_options(self):
        rec = self.svc.record
        for tid, strength in (("t1", 6.0), ("t2", 3.0), ("t3", None)):
            rec.append("thread_opened", {"thread_id": tid, "about": f"about {tid}", "raised_by": "r1",
                                         "strength": strength}, rc.TOOL)
        first = self.ok("pertura_status")["digest"]
        self.assertEqual(first["next"], ["t1: about t1", "t2: about t2", "t3: about t3"])
        self.assertIsNone(first["on"])
        self.ok("thread_status", thread_id="t1", status="followed", reason="the strongest")
        self.ok("record_finding", finding_id="f1", statement="t1 holds in both units", run_ids=["r1"], thread_id="t1",
                kind="description")
        second = self.ok("pertura_status")["digest"]
        self.assertEqual(second["on"], {"thread_id": "t1", "about": "about t1", "why": "the strongest"})
        self.assertEqual(len(second["since"]), 1)  # only what happened since the first digest
        self.assertIn("finding f1", second["since"][0])
        self.assertTrue(second["since"][0].startswith("t1 (about t1)"))
        self.assertIn("1 finding without your reply", second["awaiting_your_reply"])  # it does not stop the agent
        self.assertTrue(second["waits_on_you"][0].startswith("the design"))  # this project's design is not confirmed
        self.assertTrue(second["line"].startswith("on t1: about t1"))
        self.assertEqual(self.ok("pertura_status")["digest"]["since"], [])

    def test_the_design_waits_until_it_is_confirmed(self):
        st = rc.reduce([])
        self.assertEqual(digest.digest(st, [], design_confirmed=False)["waits_on_you"][0].split(":")[0], "the design")


if __name__ == "__main__":
    unittest.main()
