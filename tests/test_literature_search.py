"""search_literature (pertura.literature; agreed with the user on 2026-11-02): papers from Europe PMC for a query, the
workspace's kept-out papers withheld (a test session's own screen's papers, so it cannot read the findings it is scored
on), the search recorded as prior knowledge.
- a reply's paper: title, first author, journal, year, PMID or DOI and the start of its abstract;
- a paper kept out by its PMID, its DOI or a phrase in its title is withheld, and the reply counts it;
- words are matched in titles and abstracts, then the full text fills what is short, without meeting-abstract books,
  each paper saying where it matched; a long query no title or abstract holds gets a hint (after
  Pertura's searches of 9 to 18 words returned meeting-abstract books);
- a PMID or a DOI alone looks the paper up; one among other words is refused, with the way;
- the service records the search and the papers it returned as prior knowledge;
- a service that does not answer is said;
- with PERTURA_NETWORK_TESTS=1, one live search."""
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

from test_design_service import make_deposit

from pertura import literature
from pertura import record as rc
from pertura.analysis_service import AnalysisService

RESULTS = {"resultList": {"result": [
    {"id": "1", "source": "MED", "pmid": "111", "doi": "10.1/a", "title": "EGR1 drives TCF7 in MAPK signalling",
     "authorString": "Smith A, Jones B.", "journalInfo": {"journal": {"title": "Nucleic Acids Res"}}, "pubYear": "2026",
     "abstractText": "x " * 400},
    {"id": "PPR2", "source": "PPR", "doi": "10.1101/b", "title": "A preprint on FOS", "authorString": "Lee C.",
     "pubYear": "2025", "abstractText": "short"},
    {"id": "3", "source": "MED", "pmid": "333", "title": "Perturb-seq reveals TCF7 as a transcriptional link",
     "authorString": "Klinger B.", "pubYear": "2026"}]}}


def opener_of(payload):
    def opener(url, timeout=None):
        opener.url = url
        return io.BytesIO(json.dumps(payload).encode("utf-8"))
    return opener


class TestSearch(unittest.TestCase):
    def test_a_paper_carries_what_a_reader_needs_and_the_kept_out_ones_are_withheld(self):
        out = literature.search("EGR1 TCF7", 5, {"pmids": ["111"], "title_words": ["perturb-seq reveals"]},
                                opener=opener_of(RESULTS))
        self.assertEqual(out["withheld"], 2)
        (p,) = out["papers"]
        self.assertEqual((p["id"], p["first_author"], p["journal"], p["year"], p["preprint"]),
                         ("DOI:10.1101/b", "Lee C.", "preprint", "2025", True))
        out = literature.search("EGR1 TCF7", 5, None, opener=opener_of(RESULTS))
        self.assertEqual(out["withheld"], 0)
        self.assertEqual(out["papers"][0]["id"], "PMID:111")
        self.assertTrue(out["papers"][0]["abstract"].endswith("...")
                        and len(out["papers"][0]["abstract"]) <= literature.ABSTRACT_CHARS + 3)
        self.assertEqual(literature.search("x", 1, {"dois": ["10.1/A"]}, opener=opener_of(RESULTS))["withheld"], 1)

    def test_titles_and_abstracts_first_then_the_full_text_without_abstract_books(self):
        self.assertEqual(literature.queries("EGR1 TCF7"),
                         [("TITLE_ABS:(EGR1 TCF7)", literature.IN_TITLES),
                          ('(EGR1 TCF7) NOT PUB_TYPE:"abstract"', literature.IN_FULL_TEXT)])
        for words in ("PMID:35953545", "pmid 35953545", "35953545"):
            self.assertEqual(literature.queries(words), [("EXT_ID:35953545 AND SRC:MED", None)])
        self.assertEqual(literature.queries("doi:10.1101/2022.04.20.488974"),
                         [('DOI:"10.1101/2022.04.20.488974"', None)])
        self.assertEqual(literature.queries('AUTH:"Uhlitz F" ERK'), [('AUTH:"Uhlitz F" ERK', None)])
        with self.assertRaises(literature.LiteratureError) as e:
            literature.queries("PMID:42893407; ARID2 SWI/SNF microglia state")
        self.assertIn("alone looks that paper up", str(e.exception))

        def two_answers(url, timeout=None):  # one paper in titles and abstracts, the rest in full texts only
            titles = "TITLE_ABS" in urllib.parse.unquote_plus(url)
            two_answers.urls.append(url)
            return io.BytesIO(json.dumps({"resultList": {"result": RESULTS["resultList"]["result"][:1] if titles
                                                         else RESULTS["resultList"]["result"]}}).encode("utf-8"))
        two_answers.urls = []
        out = literature.search("EGR1 TCF7", 5, None, opener=two_answers)
        self.assertEqual([(p["id"], p["matched"]) for p in out["papers"]],
                         [("PMID:111", "title or abstract"), ("DOI:10.1101/b", "full text only"),
                          ("PMID:333", "full text only")])
        self.assertEqual((len(two_answers.urls), "hint" in out), (2, False))
        two_answers.urls = []
        self.assertEqual(len(literature.search("EGR1 TCF7", 1, None, opener=two_answers)["papers"]), 1)
        self.assertEqual(len(two_answers.urls), 1)  # the titles and abstracts gave enough

        def none_in_titles(url, timeout=None):
            titles = "TITLE_ABS" in urllib.parse.unquote_plus(url)
            return io.BytesIO(json.dumps({"resultList": {"result": [] if titles
                                                         else RESULTS["resultList"]["result"]}}).encode("utf-8"))
        long = literature.search("ERK signalling induces immediate early genes EGR1 FOS Raf", 5, None,
                                 opener=none_in_titles)
        self.assertIn("two to four words", long["hint"])
        self.assertNotIn("hint", literature.search("EGR1 TCF7", 5, None, opener=none_in_titles))

    def test_a_service_that_does_not_answer_is_said(self):
        def down(url, timeout=None):
            raise OSError("no route")
        with self.assertRaises(literature.LiteratureError) as e:
            literature.search("EGR1", opener=down)
        self.assertIn("did not answer", str(e.exception))
        with self.assertRaises(literature.LiteratureError):
            literature.search("  ")

    @unittest.skipUnless(os.environ.get("PERTURA_NETWORK_TESTS") == "1", "set PERTURA_NETWORK_TESTS=1 to call Europe PMC")
    def test_one_live_search(self):
        out = literature.search("EGR1 MAPK immediate early", 3)
        self.assertTrue(out["papers"] and all(p["title"] for p in out["papers"]))


class TestTheTool(unittest.TestCase):
    def test_the_service_withholds_the_workspaces_kept_out_papers_and_records_the_search(self):
        with tempfile.TemporaryDirectory() as d:
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            svc = AnalysisService({"workspace": os.path.join(d, "ws"), "literature_excluded": {"pmids": ["111", "333"]}})
            try:
                ok, out = svc.call("open_project", {"data_folder": deposit})
                self.assertTrue(ok, out)
                with mock.patch("urllib.request.urlopen", opener_of(RESULTS)):
                    ok, out = svc.call("search_literature", {"query": "EGR1 TCF7"})
                self.assertTrue(ok, out)
                self.assertEqual(([p["id"] for p in out["papers"]], out["withheld"], out["tag"]),
                                 (["DOI:10.1101/b"], 2, rc.PRIOR))
                kept = [k for k in svc.record.state()["knowledge"] if k["question_id"] == "literature"]
                self.assertEqual((kept[0]["item"], kept[0]["text"]), ("search: EGR1 TCF7", "DOI:10.1101/b"))
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
