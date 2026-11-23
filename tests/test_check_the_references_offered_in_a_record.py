"""The check of the knowledge-base references offered with next steps
(analysis/task_evaluation/knowledge_references/check_the_references_offered_in_a_record.py): references resolved
to the store's rows, a kept-out paper's row found as a leak, counts recomputed, and a blinded sheet whose decoys are
the same move after other kinds, scored against its key. Skipped until the store offers went_on_to (ADR 0050, D5)."""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

from pertura import knowledge_store as ks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "analysis", "task_evaluation", "knowledge_references",
                      "check_the_references_offered_in_a_record.py")
if not os.path.exists(SCRIPT):  # the evaluation's scripts stay on the laptop (scripts/deploy_to_ultra.sh)
    raise unittest.SkipTest("the evaluation's scripts stay on the laptop")
spec = importlib.util.spec_from_file_location("check_refs", SCRIPT)
check_refs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_refs)


def row(paper, label, work, step, kind, panels, raised_by=()):
    return {"paper": paper, "work": work, "label": label, "modality": "CRISPRi", "system": "cell line",
            "readout": "transcriptome", "step": step, "question": f"step {step}", "raised_by": list(raised_by),
            "result": f"result {step}", "quote": f"quote {step}", "checks": [], "doi": None, "pmid": str(paper),
            "notes": None, "title": label,
            "evidence": [{"analysis": kind, "panels": panels, "evidence": "a chart", "compared_with": None}]}


ROWS = [row(1, "PMID1 Alpha 2024", "wA", 1, "states", "Fig 2a"),
        row(1, "PMID1 Alpha 2024", "wA", 2, "genes", "Fig 3b", raised_by=[1]),
        row(2, "PMID2 Beta 2023", "wB", 1, "states", "Fig 1c"),
        row(2, "PMID2 Beta 2023", "wB", 2, "enrichment", "Fig 4a", raised_by=[1]),
        row(3, "PMID3 Gamma 2022", "wC", 1, "similarity", "Fig 2b"),
        row(3, "PMID3 Gamma 2022", "wC", 2, "genes", "Fig 5d", raised_by=[1]),
        row(4, "PMID4 Delta 2021", "wD", 1, "states", "Fig 1a"),
        row(4, "PMID4 Delta 2021", "wD", 2, "genes", "Fig 2c", raised_by=[1])]
KEPT_OUT = {"pmids": ["4"]}


def results(path=None, excluded=None):
    return [x for x in ROWS if not excluded or x["pmid"] not in excluded.get("pmids", [])]


def ref(paper, step, label, panels):
    return {"row": f"{paper}:{step}", "paper": label, "step": step, "panels": panels,
            "source": f"https://pubmed.ncbi.nlm.nih.gov/{paper}/"}


def state(n=1, m=2, refs=None):
    step = {"rank": 1, "recommended": True, "question": "Which genes move within S04?", "move": "genes", "why": "x",
            "counts": {"n": n, "m": m, "situation": "after a result of states"},
            "references": refs if refs is not None else [ref(1, 2, "PMID1 Alpha 2024", "Fig 3b")]}
    return {"results": {"r1": {"title": "S04 grows", "text": "", "decision": None, "next_steps": [step]}}}


@unittest.skipUnless(hasattr(ks, "went_on_to"), "the store's went_on_to lands with ADR 0050's item e")
class TestTheReferencesOffered(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(ks, "results", side_effect=results)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_right_offer_resolves_did_the_move_and_counts_right(self):
        out = check_refs.check(state(), "store", KEPT_OUT)
        self.assertEqual(out["summary"], {"steps_offered": 1, "steps_counted_right": 1, "references": 1,
                                          "resolved": 1, "did_the_move": 1, "kept_out_leaks": 0})

    def test_counts_that_differ_and_a_reference_that_does_not_match_its_row(self):
        out = check_refs.check(state(n=2, refs=[ref(1, 2, "PMID1 Alpha 2024", "Fig 9z")]), "store", KEPT_OUT)
        self.assertEqual(out["steps"][0]["recomputed"], {"n": 1, "m": 2})
        self.assertFalse(out["steps"][0]["counted"])
        self.assertFalse(out["steps"][0]["references"][0]["resolved"])

    def test_a_kept_out_papers_row_is_a_leak(self):
        out = check_refs.check(state(refs=[ref(4, 2, "PMID4 Delta 2021", "Fig 2c")]), "store", KEPT_OUT)
        self.assertEqual(out["summary"]["kept_out_leaks"], 1)
        self.assertEqual(out["summary"]["resolved"], 0)

    def test_a_row_of_another_move_or_situation_did_not_make_the_move(self):
        out = check_refs.check(state(refs=[ref(3, 2, "PMID3 Gamma 2022", "Fig 5d")]), "store", KEPT_OUT)
        self.assertTrue(out["steps"][0]["references"][0]["resolved"])
        self.assertFalse(out["steps"][0]["references"][0]["did_the_move"])  # genes, but after similarity

    def test_the_sheet_mixes_decoys_of_the_same_move_after_other_kinds_and_scores_against_its_key(self):
        items, key = check_refs.sheet(state(), "store", KEPT_OUT, n_decoys=2, seed=0)
        self.assertEqual(len(items), 1)
        rows = {v["row"]: v["is"] for v in key["1"].values()}
        self.assertEqual(rows, {"1:2": "real", "3:2": "decoy"})  # 4:2 is kept out; 2:2 is another move
        self.assertNotIn("is", str(items))  # the sheet says nothing of which is which
        with tempfile.TemporaryDirectory() as d:
            check_refs.write_sheet(items, key, d)
            self.assertTrue(all(os.path.exists(os.path.join(d, f))
                                for f in ("rating_sheet.md", "ratings.csv", "key.json")))
        ratings = [(1, letter, "relevant" if v["is"] == "real" else "not") for letter, v in key["1"].items()]
        got = check_refs.score(ratings, key)
        self.assertEqual((got["real"]["share_relevant"], got["decoy"]["share_relevant"]), (1.0, 0.0))


if __name__ == "__main__":
    unittest.main()
