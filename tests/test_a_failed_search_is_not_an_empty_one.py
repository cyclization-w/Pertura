"""A literature search that failed is not one that found nothing (docs/adr/0047, A4). GSE342764 (194f862) called two
results "new as far as the literature shows" while the searches for the genes that mattered had timed out.
- a request that timed out is asked again;
- a search that failed is recorded as not searched;
- nothing is called new while a failed search has not been run again."""
import io
import json
import unittest
from unittest import mock

from pertura import literature
from pertura import record as rc
from pertura.analysis_service import AnalysisError, AnalysisService


class TestTheSearch(unittest.TestCase):
    def test_a_timed_out_request_is_asked_again(self):
        calls = []

        def slow_then_fine(url, timeout=None):
            calls.append(url)
            if len(calls) < 3:
                raise TimeoutError("timed out")
            return io.BytesIO(json.dumps({"resultList": {"result": []}}).encode("utf-8"))
        with mock.patch.object(literature, "PAUSE", 0):
            out = literature.search("ZEB2 CD8", opener=slow_then_fine)
        self.assertEqual(calls.count(calls[0]), 3)  # titles and abstracts, asked three times; then the full text
        self.assertEqual(out["papers"], [])

    def test_a_service_that_never_answers_raises_no_answer(self):
        def down(url, timeout=None):
            raise TimeoutError("timed out")
        with mock.patch.object(literature, "PAUSE", 0), self.assertRaises(literature.NoAnswer) as e:
            literature.search("ZEB2 CD8", opener=down)
        self.assertIn("did not answer", str(e.exception))
        self.assertTrue(issubclass(literature.NoAnswer, literature.LiteratureError))


class Service:
    """The analysis service's search_literature over a record that keeps what it is given."""
    search_literature = AnalysisService.search_literature
    cfg = {}

    def __init__(self):
        self.appended = []
        outer = self

        class Record:
            @staticmethod
            def append(kind, payload, actor):
                outer.appended.append((kind, payload, actor))
        self.record = Record

    def _need(self):
        pass


class TestTheRecord(unittest.TestCase):
    def test_a_failed_search_is_recorded_as_not_searched(self):
        svc = Service()
        with mock.patch.object(literature, "search", side_effect=literature.NoAnswer("Europe PMC did not answer")):
            with self.assertRaises(AnalysisError) as e:
                svc.search_literature("  Zeb2 CD8 ")
        self.assertIn("not a search that found nothing", str(e.exception))
        kind, payload, actor = svc.appended[0]
        self.assertEqual((kind, payload["item"], actor), ("knowledge_recorded", "search failed: Zeb2 CD8", rc.TOOL))
        self.assertTrue(payload["text"].startswith("not searched:"))

    def test_a_query_refused_for_its_words_is_not_recorded(self):
        svc = Service()
        with mock.patch.object(literature, "search", side_effect=literature.LiteratureError("give a query")):
            with self.assertRaises(AnalysisError):
                svc.search_literature("")
        self.assertEqual(svc.appended, [])


class TestNothingIsNewWhileASearchFailed(unittest.TestCase):
    def standing(self):
        return [{"relation": "GENEA raises S04", "standing": rc.NEW, "sources": ["search: genea geneb"]}]

    def knowledge(self, *items):
        return {"knowledge": [{"question_id": "literature", "item": i, "text": t} for i, t in items]}

    def test_new_waits_for_a_failed_search(self):
        st = self.knowledge(("search: GENEA GENEB", "none"), ("search failed: Zeb2 CD8", "not searched: timed out"))
        problems = rc.standing_problems(st, self.standing())
        self.assertTrue(any("failed have been run again: zeb2 cd8" in p for p in problems), problems)

    def test_new_stands_once_the_failed_search_ran(self):
        st = self.knowledge(("search: GENEA GENEB", "none"), ("search failed: Zeb2 CD8", "not searched: timed out"),
                            ("search: zeb2  CD8", "none"))
        self.assertEqual(rc.standing_problems(st, self.standing()), [])


if __name__ == "__main__":
    unittest.main()
