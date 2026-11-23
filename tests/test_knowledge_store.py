"""The knowledge base's store and its three queries (docs/build/the_knowledge_base_from_scratch_2026-11-11.md): a
paper's results checked and written in one transaction, a link to a result that failed removed, the earlier rows
exported with how they were read; the queries count studies, answer from the links papers state, and give the screen's
design first among their examples."""
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from test_paper_reading_scripts import FIGURES_JATS, LABEL, Library, papers  # the reading tests' library and paper

sys.path.insert(0, os.path.dirname(os.path.abspath(papers.__file__)))
import store  # noqa: E402

from pertura import kinds  # noqa: E402
from pertura import knowledge_store as ks  # noqa: E402

RESULT_1 = "Knockout of GENE1 lowered its own transcript in the cells that carry its guides, in all four donors"
RESULT_3 = "The screen captured a median of 200 cells per guide across the library"


def item(**given):
    return {"panels": "Fig. 1b-c", "evidence": "a target's own gene", "analysis": ["guides"],
            "method": "a mixture model of guide counts", "method_place": "Methods: Guide assignment",
            "compared_with": "control cells", "unit": "cells", "chart": "box or violin with points",
            "colour": "groups", "colour_named_by": "a key", "targets_shown": "one or a few targets", "image": "none",
            **given}


def result(step, quote, raised_by=(), evidence=None, checks=("guides agree",)):
    return {"step": step, "question": "whether the guides lower their target", "raised_by": list(raised_by),
            "result": "the guides lower their target", "result_quote": quote, "checks_reported": list(checks),
            "evidence": evidence or [item()]}


class TestTheStore(Library):
    def setUp(self):
        super().setUp()
        self.cut_paper(FIGURES_JATS)
        self.db = os.path.join(self.lib, store.STORE)
        self.brief = os.path.join(self.tmp.name, "results-reader.md")
        papers.write_text(self.brief, "the brief\n")

    def reading(self, results, design=None):
        design = design or {"modality": "CRISPR knockout", "system": "primary cells", "readout": "transcriptome"}
        papers.write_text(os.path.join(self.lib, "rows", "pmid123", store.READING),
                          json.dumps({"paper": LABEL, "design": design, "results": results}))

    def write(self):
        return store.write_paper(self.lib, "pmid123", "claude-sonnet-5-5", brief_path=self.brief)

    def rows(self):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT step, raised_by, notes FROM results ORDER BY step").fetchall()

    def three_results(self):
        ext = item(panels="Extended Data Fig. 3a", evidence="cells, guides and quality", chart="bar per group",
                   colour="one colour", colour_named_by="not applicable", targets_shown="every target tested")
        return [result(1, RESULT_1), result(2, "A sentence that this paper never wrote about anything at all."),
                result(3, RESULT_3, raised_by=[1, 2], evidence=[ext])]

    def test_the_results_that_pass_are_written_and_a_link_to_one_that_failed_is_removed(self):
        self.reading(self.three_results())
        out = self.write()
        self.assertEqual(out[0], f"pmid123 ({LABEL}): 2 of 3 results pass their checks")
        self.assertIn("  step 2 fails: result_quote is not in the text", out)
        self.assertIn("  step 3: raised by step 2 too, which failed its checks", out)
        self.assertEqual(self.rows(), [(1, "[]", ""), (3, "[1]", "raised by step 2 too, which failed its checks")])
        with closing(sqlite3.connect(self.db)) as db:
            work, read_with = db.execute("SELECT work, read_with FROM papers").fetchone()
        how = json.loads(read_with)
        self.assertEqual(work, "10.1000/zhao")
        self.assertEqual(set(how), {"brief_sha256", "model", "text_sha256", "images_sha256", "date"})
        self.assertEqual((how["model"], how["images_sha256"]), ("claude-sonnet-5-5", "none"))

    def test_a_missing_unit_is_preserved_without_making_one_up(self):
        self.reading([result(1, RESULT_1, evidence=[item(unit="not stated")])])
        self.write()
        out = ks.query("analysis", "guides", path=self.db)
        self.assertEqual(out["counts"]["unit"], [("not stated", 1)])
        self.assertEqual(out["examples"][0]["evidence"][0]["unit"], "not stated")

    def test_plain_caption_markers_accept_real_panels_and_still_reject_an_absent_panel(self):
        path = os.path.join(self.lib, "texts", "pmid123", "results.txt")
        body = papers.read_text(path).split("# Figure and supplementary legends", 1)[0]
        papers.write_text(path, body + "# Figure and supplementary legends\n"
                          "Fig. 1. Target measurements. a Design of the comparison. "
                          "b Expression of the target. c Expression in control cells.\n")
        self.reading([result(1, RESULT_1), result(2, RESULT_1, evidence=[item(panels="Fig. 1z")])])
        report = store.check_paper(self.lib, "pmid123")
        self.assertEqual(report["accepted_results"], 1)
        self.assertEqual(report["issues"], [{"step": 2, "errors": ["evidence 1: panel z is not in the legend of Fig. 1"]}])

    def test_a_long_literal_quote_is_kept_but_a_paraphrase_is_rejected(self):
        quote = " ".join(["source"] * 90)
        path = os.path.join(self.lib, "texts", "pmid123", "results.txt")
        papers.write_text(path, quote + "\n" + papers.read_text(path))
        self.reading([result(1, quote)])
        self.assertEqual(store.check_paper(self.lib, "pmid123")["status"], "ready")
        self.reading([result(1, quote.replace("source", "invented", 1))])
        self.assertIn("not in the text", store.check_paper(self.lib, "pmid123")["issues"][0]["errors"][0])
        self.reading([result(1, " ".join(["source"] * 121))])
        self.assertIn("not 8 to 120", store.check_paper(self.lib, "pmid123")["issues"][0]["errors"][0])

    def test_retry_feedback_archives_each_revision_and_does_not_write_the_store(self):
        self.reading([])
        first = store.check_paper(self.lib, "pmid123")
        self.assertEqual(first["status"], "retry")
        self.assertIn("no results", first["paper_issues"][0])
        self.reading([result(1, RESULT_1)])
        second = store.check_paper(self.lib, "pmid123")
        self.assertEqual(second["status"], "ready")
        self.assertNotEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(store.check_paper(self.lib, "pmid123")["snapshot"], second["snapshot"])
        self.assertFalse(os.path.exists(self.db))
        self.assertEqual(json.loads(papers.read_text(os.path.join(self.lib, first["snapshot"])))['results'], [])
        self.assertEqual(len(os.listdir(os.path.dirname(os.path.join(self.lib, first["snapshot"])))), 2)

    def test_bad_secondary_evidence_is_excluded_without_losing_the_claim_or_its_child(self):
        first = result(1, RESULT_1, evidence=[item(), item(colour="one colour")])
        second = result(2, RESULT_3, raised_by=[1])
        self.reading([first, second])
        report = store.check_paper(self.lib, "pmid123")
        self.assertEqual((report["status"], report["accepted_results"], report["accepted_evidence"]),
                         ("retry", 2, 2))
        self.assertIn("excluded evidence 2", report["warnings"][0]["notes"])
        self.write()
        self.assertEqual(self.rows()[1], (2, "[1]", ""))
        queried = ks.query("proceed", "guides", path=self.db)
        self.assertEqual(queried["examples"][0]["previous"][0]["step"], 1)
        figure = ks.query("figure", "guides", path=self.db)
        self.assertEqual(len(figure["examples"][0]["evidence"]), 1)
        self.assertEqual(len(json.loads(papers.read_text(os.path.join(self.lib, "rows", "pmid123",
                                                                    store.READING)))["results"][0]["evidence"]), 2)

    def test_bad_quote_or_source_is_not_rescued_by_valid_evidence(self):
        for change in ({"result_quote": "A sentence that this paper never wrote about anything at all."},
                       {"result": "As Leftout 2026 found"}):
            self.reading([{**result(1, RESULT_1, evidence=[item(), item(colour="one colour")]), **change}])
            report = store.check_paper(self.lib, "pmid123")
            self.assertEqual(report["accepted_results"], 0)
            self.assertTrue(report["issues"])
        self.reading([result(1, RESULT_1, evidence=[item(), item(colour="one colour", method="Leftout 2026")])])
        self.assertEqual(store.check_paper(self.lib, "pmid123")["accepted_results"], 0)

    def test_invalid_json_gets_retry_feedback_and_a_raw_snapshot(self):
        path = os.path.join(self.lib, "rows", "pmid123", store.READING)
        papers.write_text(path, '{"paper":')
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(store.main(["check", self.lib, "pmid123"]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "retry")
        self.assertIn("invalid reading JSON", report["paper_issues"][0])
        snapshots = os.listdir(os.path.join(self.lib, "rows", "pmid123", "attempts"))
        self.assertEqual(len(snapshots), 1)

    def test_an_accepted_reading_reaches_the_agent_with_its_quote_and_method(self):
        from pertura.analysis_service import AnalysisService
        self.reading(self.three_results())
        self.write()
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.addCleanup(svc.close)
        with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.lib}):
            ok, out = svc.call("read_knowledge", {"query": "analysis", "kind": "guides"})
            self.assertTrue(ok, out)
            self.assertEqual(out["examples"][0]["quote"], RESULT_1)
            self.assertEqual(out["examples"][0]["evidence"][0]["method_place"], "Methods: Guide assignment")
            ok, out = svc.call("read_knowledge", {"query": "proceed", "kind": "guides"})
            self.assertTrue(ok, out)
            self.assertEqual(out["examples"][0]["previous"][0]["quote"], RESULT_1)
            self.assertIn("failed its checks", out["examples"][0]["notes"])

    def test_each_check_of_a_result_can_fail(self):
        cases = [
            ("a list", item(chart="pie"), "evidence 1: chart 'pie' is not one of the list"),
            ("the analysis", item(analysis=["astrology"]), "evidence 1: analysis ['astrology'] is not one or more of "
                                                          "the list"),
            ("one colour named", item(colour="one colour"), "evidence 1: one colour is named nowhere, so its "
                                                            "colour_named_by is `not applicable`, and only then"),
            ("a panel", item(panels="Fig. 1e"), "evidence 1: panel e is not in the legend of Fig. 1"),
            ("a figure", item(panels="Fig. 5a"), "evidence 1: no legend of Fig. 5 in the text"),
            ("an image", item(image="f1.jpg"), "evidence 1: image 'f1.jpg' is not one of the paper's figure images; a "
                                               "figure without one is `none`"),
            ("the method's place", item(method_place="Methods: Cell culture"),
             "evidence 1: method_place 'Methods: Cell culture' is not a heading of the paper's Methods")]
        for name, given, why in cases:
            with self.subTest(name):
                self.reading([result(1, RESULT_1, evidence=[given])])
                self.assertIn(f"  step 1 fails: {why}", self.write())
        for name, r, why in (("a check", {**result(1, RESULT_1), "checks_reported": ["vibes"]},
                              "checks_reported ['vibes'] are not values of the list"),
                             ("a later step", result(1, RESULT_1, raised_by=[1]),
                              "raised_by names a step that is not earlier"),
                             ("a source kept out", {**result(1, RESULT_1), "result": "as Leftout 2026 found"},
                              "cites sources kept out: Leftout 2026")):
            with self.subTest(name):
                self.reading([r])
                self.assertIn(f"  step 1 fails: {why}", self.write())
        self.reading([result(1, RESULT_1)], design={"modality": "telepathy", "system": "cell line",
                                                     "readout": "transcriptome"})
        self.assertEqual(self.write()[-1], "  not written: design modality 'telepathy' is not one of the list")

    def test_a_method_can_be_reported_in_a_figure_legend(self):
        self.reading([result(1, RESULT_1, evidence=[item(method_place="Fig. 1b legend")])])
        self.assertIn("  written: 1 results, read by claude-sonnet-5-5", self.write())
        for place in ("Fig. 1e legend", "Fig. 5a legend"):
            self.reading([result(1, RESULT_1, evidence=[item(method_place=place)])])
            self.assertIn(f"  step 1 fails: evidence 1: method_place {place!r} is not in the supplied figure legends",
                          self.write())
        legends = {("supp", 2): {"letters": set("abcde")}}
        self.assertEqual(store.place_fails("Fig. S2E", [], legends), [])
        self.assertTrue(store.place_fails("Fig. S2F", [], legends))

    def test_a_method_stated_in_results_has_to_name_a_real_results_heading(self):
        path = os.path.join(self.lib, "texts", "pmid123", "results.txt")
        papers.write_text(path, "## Calculation stated here\nA test on cells.\n\n" + papers.read_text(path))
        self.reading([result(1, RESULT_1, evidence=[item(method_place="Results: Calculation stated here")])])
        self.assertIn("  written: 1 results, read by claude-sonnet-5-5", self.write())
        for place in ("Results: Guide assignment", "Results: Invented calculation"):
            self.reading([result(1, RESULT_1, evidence=[item(method_place=place)])])
            self.assertIn(f"  step 1 fails: evidence 1: method_place {place!r} is not a heading of the paper's Results",
                          self.write())

    def test_the_source_title_is_stored_for_title_based_exclusions(self):
        source = os.path.join(self.lib, "sources", "pmid123", "fulltext.xml")
        text = papers.read_text(source)
        text = text.replace("<article>", "<article><front><article-meta><title-group>"
                            "<article-title>A source title with <italic>markup</italic></article-title>"
                            "</title-group></article-meta></front>", 1)
        papers.write_text(source, text)
        self.reading([result(1, RESULT_1)])
        self.write()
        with closing(sqlite3.connect(self.db)) as db:
            title = db.execute("SELECT title FROM papers").fetchone()[0]
        self.assertEqual(title, "A source title with markup")

    def test_a_legacy_store_gains_titles_without_losing_its_rows(self):
        old_schema = store.SCHEMA.replace(", title TEXT NOT NULL DEFAULT ''", "")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript(old_schema)
            db.execute("INSERT INTO papers VALUES ('old', 'study', 'Old paper', '', '', '', '', '', '', '{}')")
        with closing(store.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT id, title FROM papers").fetchall(), [("old", "")])

    def test_a_paper_read_again_replaces_its_rows_after_exporting_them_and_a_write_that_stops_changes_nothing(self):
        self.reading(self.three_results())
        self.write()
        self.reading([result(1, RESULT_1)])
        out = self.write()
        self.assertTrue(any("its earlier 2 results exported to exports" in line for line in out), out)
        self.assertEqual(self.rows(), [(1, "[]", "")])
        exported = os.listdir(os.path.join(self.lib, store.EXPORTS))
        with open(os.path.join(self.lib, store.EXPORTS, exported[0]), encoding="utf-8") as fh:
            old = json.load(fh)
        self.assertEqual(([x["step"] for x in old["results"]], old["read_with"]["model"]), ([1, 3], "claude-sonnet-5-5"))

        class Stops:  # a connection whose rows' insert fails halfway
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def __enter__(self):
                return self.db.__enter__()

            def __exit__(self, *given):
                return self.db.__exit__(*given)

            def executemany(self, *given):
                raise sqlite3.OperationalError("disk I/O error")
        real = store.connect
        self.reading(self.three_results())
        with mock.patch.object(store, "connect", lambda path: Stops(real(path))):
            with self.assertRaises(sqlite3.OperationalError):
                self.write()
        self.assertEqual(self.rows(), [(1, "[]", "")])  # the last accepted rows, still there


def add(db, key, work, design, results, title=None):
    db.execute("INSERT INTO papers (id, work, label, doi, pmid, pmcid, modality, system, readout, read_with, title) "
               "VALUES (?, ?, ?, ?, '', '', ?, 'cell line', ?, '{}', ?)",
               (key, work, key.title(), "10.1000/" + key.replace(" ", "-"), design[0], design[1],
                key.title() if title is None else title))
    db.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')",
                   [(key, step, question, json.dumps(raised), "result: " + question, "quote: " + question,
                     json.dumps(checks), json.dumps(items))
                    for step, question, raised, checks, items in results])


def similar(chart, named):
    return item(panels="Fig. 2a", evidence="similar targets", analysis=["similarity"], chart=chart,
                colour="signed values on a diverging scale", colour_named_by=named, targets_shown="every target tested",
                unit="targets")


class TestTheQueries(unittest.TestCase):
    """Three papers, two studies: a paper and its preprint (one study, CRISPRi), and another (CRISPR knockout); each
    states that a set of targets alike raised its next step."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, store.STORE)
        db = store.connect(self.path)
        with db:
            for key in ("paper a", "preprint a"):
                add(db, key, "study a", ("CRISPRi", "transcriptome"), [
                    (1, "which targets act alike", [], ["guides agree"], [similar("correlation heatmap", "a colour bar")]),
                    (2, "what the set does", [1], [], [item(analysis=["programs"], panels="Fig. 3a")])])
            add(db, "paper b", "study b", ("CRISPR knockout", "transcriptome"), [
                (1, "which regulators act alike", [], ["replicates or donors agree"], [similar("embedding", "a key")]),
                (2, "whether the set holds in vivo", [1], [], [item(analysis=["an independent experiment"],
                                                                     panels="Fig. 4b")]),
                (3, "a question not raised by the set", [], [], [item(analysis=["enrichment"], panels="Fig. 5a")])])
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_proceed_answers_from_the_stated_links_counting_studies(self):
        a = ks.query("proceed", "similarity", path=self.path)
        self.assertEqual((a["studies"], a["counts"]["analysis"]), (2, [("an independent experiment", 1),
                                                                      ("programs", 1)]))
        self.assertEqual(a["practice"], "after a result of similarity (2 studies), papers went on to: an independent "
                                        "experiment (1), programs (1)")
        self.assertEqual([x["question"] for x in a["examples"]], ["what the set does", "whether the set holds in vivo"])
        self.assertEqual(a["examples"][0]["previous"][0]["result"], "result: which targets act alike")
        self.assertEqual(a["examples"][0]["quote"], "quote: what the set does")
        self.assertEqual(ks.query("proceed", "similar targets", path=self.path)["studies"], 2)

    def test_figure_and_analysis_count_each_study_once_and_include_the_comparison_and_source(self):
        d = ks.query("figure", "similar targets", path=self.path)
        self.assertEqual((d["studies"], d["counts"]["chart"]), (2, [("correlation heatmap", 1), ("embedding", 1)]))
        self.assertEqual(d["counts"]["colour_named_by"], [("a colour bar", 1), ("a key", 1)])
        self.assertEqual(d["examples"][0]["evidence"][0]["colour"], "signed values on a diverging scale")
        did = ks.query("analysis", "similarity", path=self.path)
        self.assertEqual((did["studies"], did["counts"]["unit"]), (2, [("targets", 2)]))
        self.assertEqual(did["counts"]["checks_reported"], [("guides agree", 1), ("replicates or donors agree", 1)])
        example = did["examples"][0]
        self.assertEqual(example["source"], "https://doi.org/10.1000/paper-a")
        self.assertEqual(example["evidence"][0]["compared_with"], "control cells")
        self.assertEqual(example["evidence"][0]["method_place"], "Methods: Guide assignment")

    def test_a_paper_of_the_screen_s_system_comes_first(self):
        """Papers of the same system (in vivo, primary cells, a cell line) are read for the same units and checks."""
        in_vivo = {"modality": "CRISPRi", "system": "in vivo", "readout": "transcriptome"}
        self.assertEqual(ks.matches(in_vivo, {"system": "in vivo"}), 1)
        self.assertEqual(ks.matches(in_vivo, {"system": "cell line", "modality": "CRISPRi"}), 1)

    def test_the_screen_s_design_comes_first_among_the_examples(self):
        knockout = ks.query("figure", "similar targets", design={"modality": "CRISPR knockout"}, path=self.path)
        self.assertEqual([x["paper"] for x in knockout["examples"]], ["Paper B", "Paper A"])
        crispri = ks.query("figure", "similar targets", design={"modality": "CRISPRi"}, path=self.path)
        self.assertEqual([x["paper"] for x in crispri["examples"]], ["Paper A", "Paper B"])

    def test_missing_and_unreadable_libraries_are_distinct_from_no_matching_paper(self):
        none = ks.query("analysis", "genes", path=os.path.join(self.tmp.name, "none.db"))
        self.assertEqual(none["status"], "unavailable")
        self.assertIn("not installed", none["practice"])
        no_match = ks.query("analysis", "genes", path=self.path)
        self.assertEqual((no_match["status"], no_match["studies"]), ("not found", 0))
        bad = os.path.join(self.tmp.name, "bad.db")
        papers.write_text(bad, "not a SQLite database")
        self.assertEqual(ks.query("figure", "similar targets", path=bad)["status"], "unavailable")

    def test_analysis_and_figure_examples_carry_what_raised_them(self):
        """An example read alone keeps the results its raised_by names; one the paper raised from nothing has none."""
        example = ks.query("analysis", "programs", path=self.path)["examples"][0]
        self.assertEqual([p["result"] for p in example["previous"]], ["result: which targets act alike"])
        example = ks.query("figure", "an independent experiment", path=self.path)["examples"][0]
        self.assertEqual([p["step"] for p in example["previous"]], [1])
        self.assertNotIn("previous", ks.query("analysis", "enrichment", path=self.path)["examples"][0])

    def test_proceed_keeps_all_the_follow_up_evidence_and_only_the_stated_parents(self):
        items = [item(analysis=["outside data"], panels="Fig. 3a"),
                 item(analysis=["programs"], panels="Fig. 3b")]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE results SET evidence = ? WHERE paper = 'paper a' AND step = 2", (json.dumps(items),))
        example = ks.query("proceed", "similarity", path=self.path)["examples"][0]
        self.assertEqual(example["evidence"], items)
        self.assertEqual([p["step"] for p in example["previous"]], [1])

    def test_proceed_counts_studies_that_state_a_follow_up(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            add(db, "no follow up", "study c", ("CRISPRi", "transcriptome"), [
                (1, "which targets act alike", [], [], [similar("correlation heatmap", "a colour bar")])])
        self.assertEqual(ks.query("analysis", "similarity", path=self.path)["studies"], 3)
        self.assertEqual(ks.query("proceed", "similarity", path=self.path)["studies"], 2)

    def test_the_three_queries_and_module_advice_reach_the_existing_mcp_tool(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.addCleanup(svc.close)
        schema = next(t for t in svc.TOOLS if t["name"] == "read_knowledge")["inputSchema"]
        self.assertEqual(schema["properties"]["query"]["enum"], ["analysis", "proceed", "figure"])
        with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}):
            for query, kind in (("analysis", "similarity"), ("proceed", "similarity"), ("figure", "similar targets")):
                with self.subTest(query=query):
                    ok, out = svc.call("read_knowledge", {"query": query, "kind": kind})
                    self.assertTrue(ok, out)
                    self.assertEqual((out["query"], out["status"], len(out["examples"])), (query, "found", 2))
            ok, out = svc.call("read_knowledge", {"module": "E.similar_targets"})
            self.assertTrue(ok, out)
            self.assertEqual(out["published_analysis"]["query"], "analysis")
            for args in ({"query": "unknown", "kind": "genes"}, {"query": "analysis"}, {"kind": "genes"}):
                ok, out = svc.call("read_knowledge", args)
                self.assertFalse(ok, out)
                self.assertNotIn("Pertura failed inside", out["error"])

    def test_a_kinds_first_run_reply_carries_no_paper_block(self):
        """GSE267740 (16ea437): 8 such blocks came after the run's unit and comparison were chosen, 6 of them to
        workers, and none was used (claude-code-d2's reading)."""
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.addCleanup(svc.close)
        with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}), \
                mock.patch.object(svc, "run_kind", return_value={"run_id": "running", "state": "running"}):
            ok, first = svc.call("similarity", {})
        self.assertTrue(ok, first)
        self.assertNotIn("published_analysis", first)

    def test_the_skeptic_gets_the_checks_papers_reported_for_a_kind(self):
        self.assertEqual(ks.checks_reported("similarity", {"modality": "CRISPRi"}, path=self.path), [
            "Paper A Fig. 2a: control cells; unit targets; a mixture model of guide counts; checks reported: guides agree",
            "Paper B Fig. 2a: control cells; unit targets; a mixture model of guide counts; checks reported: replicates "
            "or donors agree"])
        self.assertEqual(ks.checks_reported("similarity", path=self.path, k=1)[0].split(" Fig.")[0], "Paper A")
        self.assertEqual(ks.checks_reported("states", path=self.path), [])

    def test_a_drawing_worker_gets_the_charts_papers_drew_the_kind_as(self):
        self.assertEqual(ks.figure_forms(["similarity"], path=self.path), [
            "correlation heatmap: Paper A Fig. 2a, signed values on a diverging scale, named by a colour bar; every "
            "target tested",
            "embedding: Paper B Fig. 2a, signed values on a diverging scale, named by a key; every target tested"])
        self.assertEqual(ks.figure_forms(["states"], path=self.path), [])

    def test_what_papers_did_after_a_result_of_these_kinds_in_their_words(self):
        """The lead's lines at a result's next question (01f3de3: the steps shown as kinds alone were never used):
        one paper once, the closest design first, each with the result that raised the step and the step, quoted."""
        lines = ks.after_results(["similarity"], {"modality": "CRISPR knockout"}, path=self.path)
        self.assertEqual([x.split(" ")[0] + " " + x.split(" ")[1] for x in lines], ["Paper B", "Paper A"])
        self.assertIn('it went on to an independent experiment', lines[0])
        self.assertIn('it went on to programs', lines[1])
        self.assertEqual(lines[0].count('"'), 4)  # two quotes: what raised the step, and the step's result
        self.assertEqual(len(ks.after_results(["similarity", "programs"], k=1, path=self.path)), 1)
        self.assertEqual(ks.after_results(["enrichment"], path=self.path), [])  # nothing a paper says it raised
        kept = ks.after_results(["similarity"], path=self.path, excluded={"dois": ["10.1000/paper-b"]})
        self.assertEqual([x.split(" ")[1] for x in kept], ["A"])

    def test_each_kind_gives_its_first_example_before_any_kind_gives_its_second(self):
        """d2's review of 38caa1f: on batch 4 every kind has three examples, so the first kind took every line."""
        def ex(paper):
            return {"paper": paper, "quote": "q", "evidence": [item(analysis=["genes"], panels="Fig. 1")],
                    "previous": [{"quote": "p"}]}
        given = {"genes": [ex("G1"), ex("G2"), ex("G3")], "states": [ex("S1"), ex("G1")]}
        with mock.patch.object(ks, "proceed", side_effect=lambda kind, *a: {"examples": given[kind]}):
            self.assertEqual([x.split(" ")[0] for x in ks.after_results(["genes", "states"])], ["G1", "S1", "G2"])

    def test_the_service_gives_the_lead_what_papers_did_after_its_results(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.addCleanup(svc.close)
        st = {"results": {"r1": {"decision": None}, "r2": {"decision": "dropped"}}, "threads": {}}
        with mock.patch.object(svc, "_kinds_named", return_value=["similarity"]):
            with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}):
                out = svc._what_papers_did_after(st)
                self.assertEqual(list(out), ["r1"])  # a dropped result has no next question
                self.assertTrue(out["r1"][0].startswith("Paper A"))
                self.assertEqual(list(svc._what_papers_did_after({"results": {}, "threads": {}})), ["the main line"])
                two = {"results": {"r1": {"decision": None}, "r3": {"decision": None}}, "threads": {}}
                self.assertEqual(list(svc._what_papers_did_after(two)), ["r1"])  # r3's lines were shown under r1
            with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": os.path.join(self.tmp.name, "not-installed")}):
                self.assertEqual(svc._what_papers_did_after(st), {})
            with mock.patch.object(ks, "after_results", side_effect=KeyError("analysis")):  # a bad row in a batch
                self.assertEqual(svc._what_papers_did_after(st), {})  # costs the lines, not the lead's review

    def test_figure_can_match_the_results_analysis_kinds_with_one_example_limit(self):
        evidence = ks.query("figure", "similar targets", path=self.path)
        analysis = ks.query("figure", "similarity", path=self.path)
        self.assertEqual(evidence["examples"], analysis["examples"])
        with closing(sqlite3.connect(self.path)) as db, db:
            for i in range(4):
                add(db, f"another paper {i}", f"another study {i}", ("CRISPRi", "transcriptome"), [
                    (1, "a programme found", [], [], [item(analysis=["programs"])])])
        combined = ks.query("figure", ["similarity", "programs"], path=self.path)
        self.assertEqual(len(combined["examples"]), ks.EXAMPLES)
        self.assertEqual(combined["studies"], 6)

    def test_a_programme_discovery_figure_uses_its_source_choices_rather_than_the_scores_default(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.addCleanup(svc.close)
        st = {"results": {"r": {"figure": "fig", "finding_ids": ["f"]}}, "figures": {"fig": {"run_id": "source"}},
              "findings": {"f": {"question_id": "C.programs", "kind": "gene_set", "run_ids": ["source"]}},
              "runs": {"source": {"question_id": "C.programs", "what": 'module C.programs with {"choices": {"gene_sets": "cnmf"}}'}}}
        with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}):
            self.assertEqual(svc._figure_knowledge(st, ["fig"])["asked"], ["programs"])

    def test_the_service_uses_the_confirmed_modality_and_measured_protein_readout(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.addCleanup(svc.close)
        svc.project = os.path.join(self.tmp.name, "project")
        svc.design = object()
        papers.write_text(os.path.join(svc._screen_dir(), "screen.json"), '{"antibody_features": ["CD3"]}')
        confirmed = {"perturbation": {"modality": {"value": "crispr_ko"}},
                     "experiment": {"context": {"value": "in_vivo"}}}
        with mock.patch.object(svc, "_confirmed", return_value=confirmed), \
                mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}):
            self.assertEqual(svc._knowledge_design(), {"modality": "CRISPR knockout", "system": "in vivo",
                                                       "readout": "transcriptome and protein"})
            out = svc.read_knowledge(query="analysis", kind="similarity")
            self.assertEqual([x["paper"] for x in out["examples"]], ["Paper B", "Paper A"])

    def test_a_found_module_result_carries_no_paper_block(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        svc.project = os.path.join(self.tmp.name, "project")
        svc.design = mock.Mock(cfg={})
        svc.record = mock.Mock()
        runs = {r: {"run_id": r, "job_id": r, "state": "done", "what": "module E.similar_targets",
                    "question_id": "E.similar_targets"} for r in ("found", "none")}
        svc.record.state.side_effect = lambda: {"runs": runs, "plan": {"items": {}}, "knowledge": []}
        for r, status in (("found", "detected"), ("none", "not_detected")):
            papers.write_text(os.path.join(svc._jobs_root(), r, "result.json"), json.dumps({"status": status, "summary": "s"}))
        try:
            with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}):
                out = svc.run_result("found")  # the hand-written moves only; the papers' steps go to the lead
                self.assertNotIn("query", json.dumps(out.get("published_next", [])))
                self.assertNotIn("published_next", svc.run_result("none"))
        finally:
            svc.record = None
            svc.close()

    def test_the_kinds_of_analysis_are_the_service_s(self):
        self.assertEqual(store.ANALYSIS[:len(kinds.KINDS)], tuple(kinds.KINDS))

    def test_exclusions_remove_results_quotes_counts_and_versions_from_every_query(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE papers SET pmid = '12345678' WHERE id = 'paper a'")
            db.execute("UPDATE results SET result_quote = 'WITHHELD ANSWER' WHERE paper IN ('paper a', 'preprint a')")
        for excluded in ({"pmids": [12345678]}, {"dois": ["10.1000/PAPER-A"]}, {"title_words": ["paper a"]}):
            for query, kind in (("analysis", "similarity"), ("proceed", "similarity"), ("figure", "similar targets")):
                with self.subTest(excluded=excluded, query=query):
                    out = ks.query(query, kind, path=self.path, excluded=excluded)
                    self.assertEqual(out["studies"], 1)
                    self.assertEqual([e["paper"] for e in out["examples"]], ["Paper B"])
                    self.assertNotIn("WITHHELD ANSWER", json.dumps(out))
                    self.assertNotIn("Paper A", json.dumps(out))
                    self.assertNotIn("Preprint A", json.dumps(out))
                    if query == "proceed":
                        self.assertEqual(out["counts"]["analysis"], [("an independent experiment", 1)])

    def test_a_version_with_no_results_can_still_withhold_its_whole_study(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            add(db, "publication metadata", "study a", ("CRISPRi", "transcriptome"), [], title="An excluded publication")
        out = ks.query("proceed", "similarity", path=self.path,
                       excluded={"title_words": ["excluded publication"]})
        self.assertEqual([e["paper"] for e in out["examples"]], ["Paper B"])

    def test_missing_titles_are_withheld_when_title_filtering_is_required(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE papers SET title = '' WHERE work = 'study a'")
        out = ks.query("analysis", "similarity", path=self.path, excluded={"title_words": ["unrelated phrase"]})
        self.assertEqual([e["paper"] for e in out["examples"]], ["Paper B"])
        self.assertEqual(ks.query("analysis", "similarity", path=self.path,
                                  excluded={"pmids": ["unrelated"]})["studies"], 2)

    def test_a_legacy_store_without_title_metadata_can_be_filtered_read_only(self):
        legacy = os.path.join(self.tmp.name, "legacy.db")
        with closing(sqlite3.connect(legacy)) as db, db:
            db.executescript(store.SCHEMA.replace(", title TEXT NOT NULL DEFAULT ''", ""))
            for table in ("papers", "results"):
                with closing(sqlite3.connect(self.path)) as source:
                    rows = source.execute("SELECT * FROM " + table).fetchall()
                rows = [r[:-1] for r in rows] if table == "papers" else rows
                placeholders = ",".join("?" for _ in rows[0])
                db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        self.assertEqual(ks.query("analysis", "similarity", path=legacy,
                                  excluded={"title_words": ["a title"]})["studies"], 0)
        self.assertEqual(ks.query("analysis", "similarity", path=legacy,
                                  excluded={"dois": ["10.1000/paper-a"]})["studies"], 1)
        with closing(sqlite3.connect(legacy)) as db:
            self.assertNotIn("title", [r[1] for r in db.execute("PRAGMA table_info(papers)")])

    def test_invalid_exclusion_config_returns_unavailable_instead_of_unfiltered_papers(self):
        for excluded in ({"pmids": "12345678"}, {"unknown_filter": ["paper a"]}):
            out = ks.query("proceed", "similarity", path=self.path, excluded=excluded)
            self.assertEqual((out["status"], out["examples"]), ("unavailable", []))

    def test_the_service_applies_workspace_exclusions_to_direct_and_automatic_replies(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws"),
                               "literature_excluded": {"dois": ["10.1000/paper-a"]}})
        self.addCleanup(svc.close)
        with mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.tmp.name}):
            for query, kind in (("analysis", "similarity"), ("proceed", "similarity"), ("figure", "similar targets")):
                ok, out = svc.call("read_knowledge", {"query": query, "kind": kind})
                self.assertTrue(ok, out)
                self.assertEqual([e["paper"] for e in out["examples"]], ["Paper B"])
            with mock.patch.object(svc, "_kinds_named", return_value=["similarity"]):
                self.assertEqual([x.split(" Fig")[0].split(" ")[1] for x in
                                  svc._what_papers_did_after({"results": {}, "threads": {}})["the main line"]], ["B"])
            ok, out = svc.call("read_knowledge", {"query": "analysis", "kind": "similarity"})
            self.assertNotIn("practice", out)  # the agents read no counts of papers
            self.assertNotIn("counts", out)


class TestTheBrief(unittest.TestCase):
    def test_the_results_reader_s_lists_and_fields_are_the_store_s(self):
        brief = papers.read_text(os.path.join(papers.PLUGIN_FOLDER, "agents", store.BRIEF))

        def section(head):
            part = brief.split(f"\n## {head}\n", 1)[1].split("\n## ", 1)[0]
            return tuple(papers.label_of(m.group(1)) for m in re.finditer(r"^- `([^`]+)`:", part, re.M))
        self.assertEqual(section("The kinds of analysis"), store.ANALYSIS)
        self.assertEqual(section("The checks"), store.CHECKS)
        self.assertEqual(section("The units"), store.UNITS)
        for f in store.RESULT_FIELDS + store.EVIDENCE_FIELDS:  # each field named in a bullet's bold head
            self.assertRegex(brief, rf"- \*\*[a-z_, ]*\b{f}\b[a-z_, ]*:\*\*", f)
        rules = papers.read_text(os.path.join(papers.PLUGIN_FOLDER, "skills", "reading-papers", "the_rules.md"))
        self.assertIn(rules.strip(), brief)


class TestAutomaticFigureExamples(unittest.TestCase):
    def setUp(self):
        from test_results_from_findings import Service
        self.case = Service()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.svc = self.case.svc
        self.path = os.path.join(self.case.tmp.name, ks.STORE)
        with closing(store.connect(self.path)) as db, db:
            add(db, "gene paper", "gene study", ("CRISPRi", "transcriptome"), [
                (1, "which genes change", [], [], [item(analysis=["genes"], evidence="one target's effect on genes")])])
            add(db, "state paper", "state study", ("CRISPRi", "transcriptome"), [
                (1, "which states change", [], [], [item(analysis=["states"], evidence="states or composition")])])
        self.env = mock.patch.dict(os.environ, {"PERTURA_KNOWLEDGE": self.case.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reporting = mock.patch.object(self.svc, "_keep_the_report", return_value={})
        self.reporting.start()
        self.addCleanup(self.reporting.stop)
        from pertura import record as rc
        rec = self.svc.record
        for rid, module in (("genes-source", "C.genes"), ("states-source", "C.states_and_composition")):
            rec.append("run_logged", {"run_id": rid, "what": "module " + module, "question_id": module,
                                      "code": "", "inputs": []}, rc.TOOL)
            rec.append("run_finished", {"run_id": rid, "state": "ok", "output": ""}, rc.TOOL)
            rec.append("figure_saved", {"figure_id": rid + "-fig", "run_id": rid,
                                        "paths": {"png": rid + ".png"}, "caption": rid}, rc.TOOL)
            self.case.ok("record_finding", finding_id=rid + "-finding", statement="a pattern", kind="description",
                         run_ids=[rid])

    def test_the_reviewers_figure_input_delivers_examples_and_result_replies_do_not(self):
        """GSE267740 (16ea437): 11 figure blocks came in report_result replies, after the figure was proposed."""
        result = self.case.ok("report_result", result_id="genes-result", title="a gene effect",
                              finding_ids=["genes-source-finding"], figure="genes-source-fig")
        self.assertNotIn("published_figures", result)
        self.case.ok("report_result", result_id="states-result", title="a state effect",
                     finding_ids=["states-source-finding"], figure="states-source-fig")
        reviewer = self.case.ok("read_record", what="figures", ids=["genes-source-fig"])
        self.assertEqual(reviewer["published_figures"]["asked"], ["genes"])
        self.assertNotIn("practice", reviewer["published_figures"])  # no counts of papers
        self.assertNotIn("State Paper", [e["paper"] for e in reviewer["published_figures"]["examples"]])
        whole_set = self.case.ok("read_record", what="figures")
        self.assertEqual(set(whole_set["published_figures"]["asked"]), {"genes", "states"})
        self.assertLessEqual(len(whole_set["published_figures"]["examples"]), ks.EXAMPLES)

    def test_a_declared_notebook_finding_can_supply_the_figure_kind(self):
        from pertura import record as rc
        self.svc.record.append("finding_recorded", {"finding_id": "notebook-state", "statement": "a state change",
                                                   "run_ids": ["r1"], "kind": "state", "figure": "fig1"}, rc.AGENT)
        self.case.ok("report_result", result_id="notebook-result", title="state change",
                     finding_ids=["notebook-state"], figure="fig1")
        self.assertEqual(self.svc._figure_knowledge(self.svc.record.state(), ["fig1"])["asked"], ["states"])

    def test_a_workers_first_reply_carries_how_papers_did_its_threads_kinds(self):
        self.case.ok("open_thread", thread_id="t5", about="what the genes do", raised_by="genes-source-finding")
        out = self.svc._papers_for_the_worker("t5-genes-in-each-capture")
        self.assertEqual([x.split(" Fig.")[0] for x in out["how_papers_did_it"]["genes"]], ["Gene Paper"])
        self.assertEqual(self.svc._papers_for_the_worker("t55-other"), {})  # no thread of that name
        self.assertEqual(self.svc._papers_for_the_worker(None), {})  # the main agent's notebook

    def test_the_skeptic_reading_findings_gets_the_checks_papers_reported_and_a_worker_does_not(self):
        from pertura.analysis_service import AGENT_ARG
        ok, out = self.svc.call("read_record", {"what": "findings", "ids": ["genes-source-finding"],
                                                AGENT_ARG: "pertura:skeptic"})
        self.assertTrue(ok, out)
        self.assertEqual([x.split(" Fig.")[0] for x in out["checks_papers_reported"]["genes"]], ["Gene Paper"])
        ok, out = self.svc.call("read_record", {"what": "findings", "ids": ["genes-source-finding"],
                                                AGENT_ARG: "pertura:worker"})
        self.assertNotIn("checks_papers_reported", out)

    def test_the_chart_guidance_comes_once_and_no_paper_block_with_it(self):
        card = {"FG1": {"text": "## FG1. Chart guidance\nChoose the chart that shows the claimed comparison."}}
        with mock.patch("pertura.knowledge.load", return_value=card):
            first = self.case.ok("report_result", result_id="genes-result", title="a gene effect",
                                 finding_ids=["genes-source-finding"], figure="genes-source-fig")
            second = self.case.ok("report_result", result_id="states-result", title="a state effect",
                                  finding_ids=["states-source-finding"], figure="states-source-fig")
        self.assertIn("figure_forms_of_published_screens", first)
        self.assertNotIn("figure_forms_of_published_screens", second)
        self.assertNotIn("published_figures", first)

if __name__ == "__main__":
    unittest.main()
