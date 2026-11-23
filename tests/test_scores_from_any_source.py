"""Scores from any source (docs/adr/0044, D1): an MSigDB collection by its name, its sets chosen by name or by a word
in their names, and per-cell values tested as they are, in the scores block and in C.programs; the gene_sets tool
lists the sets whose names hold a word. On the synthetic screen, target B raises genes b0-b4 and every target shifts
genes s0-s9."""
import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

from test_blocks import Notebook
from test_question_modules import synthetic_screen

import pertura.blocks as pt

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
SETS = {"B_PROGRAM": [f"b{i}" for i in range(5)], "SHARED_UP": [f"s{i}" for i in range(0, 10, 2)],
        "QUIET_ONE": [f"g{i}" for i in range(5)]}


def gene_set_folder(root):
    """A folder laid out like the fetched set, holding a human cell_types collection (C8)."""
    os.makedirs(os.path.join(root, "msigdb"))
    with open(os.path.join(root, "msigdb", "c8.all.v2025.1.Hs.symbols.gmt"), "w", encoding="utf-8") as fh:
        for name, genes in SETS.items():
            fh.write("\t".join([name, "url", *genes]) + "\n")
    return root


def b_cells_raised(nb):
    """A cell column and a saved table whose value is higher in B's cells."""
    obs = nb.screen.adata.obs
    rng = np.random.default_rng(0)
    obs["my_value"] = rng.normal(0, 1, len(obs)) + np.where(obs["label"] == "B", 3.0, 0.0)
    return pd.DataFrame({"barcode": obs.index.astype(str), "raised_in_b": obs["my_value"].to_numpy()})


class TestTheChoiceOfSets(unittest.TestCase):
    """What the scores block scores of a collection, before any scoring."""

    def test_a_word_a_list_and_a_collection_too_large_to_score_whole(self):
        chosen = pt._chosen_sets(SETS, "program", "cell_types")
        self.assertEqual(list(chosen), ["B_PROGRAM"])  # the word, whatever its case
        self.assertEqual(list(pt._chosen_sets(SETS, ["SHARED_UP"], "cell_types")), ["SHARED_UP"])
        with self.assertRaisesRegex(ValueError, r"no set \['NOPE'\]"):
            pt._chosen_sets(SETS, ["NOPE"], "cell_types")
        with self.assertRaisesRegex(ValueError, "no set of cell_types has 'zzz'"):
            pt._chosen_sets(SETS, "zzz", "cell_types")
        with mock.patch.object(pt, "MAX_SETS", 2):
            with self.assertRaisesRegex(ValueError, "holds 3 sets.*give select"):
                pt._chosen_sets(SETS, None, "cell_types")
            with self.assertRaisesRegex(ValueError, "3 sets of cell_types have '_' in their names"):
                pt._chosen_sets(SETS, "_", "cell_types")
        self.assertEqual(pt._chosen_sets(SETS, None, "cell_types"), SETS)  # small enough to score whole

    def test_c_programs_takes_a_collection_and_cell_values_only_where_they_are_used(self):
        from pertura.question_modules import REGISTRY
        m = REGISTRY["C.programs"]
        m.check({"gene_sets": "cell_types", "select": "PROGRAM"})
        m.check({"gene_sets": "cell_values", "cell_values": "my_value"})
        with self.assertRaisesRegex(ValueError, "select is used only with gene_sets"):
            m.check({"gene_sets": "cnmf", "select": "PROGRAM"})
        with self.assertRaisesRegex(ValueError, "cell_values is used only with gene_sets"):
            m.check({"gene_sets": "hallmark", "cell_values": "my_value"})


class TestTheGeneSetsToolListsNamesByAWord(unittest.TestCase):
    def test_match_gives_the_names_and_sizes_and_records_nothing(self):
        from pertura.analysis_service import AnalysisService
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": gene_set_folder(d)}):
            svc = SimpleNamespace(_need=lambda: None, _organism=lambda: "human", record=mock.Mock())
            out = AnalysisService.gene_sets(svc, collection="cell_types", match="up")
        self.assertEqual(out["sets_and_sizes"], {"SHARED_UP": 5})
        self.assertEqual(out["sets"], 1)
        svc.record.append.assert_not_called()  # names to choose from, not knowledge put beside a finding


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestScoresFromAnySource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": gene_set_folder(self.tmp.name)})
        self.env.start()
        self.nb = Notebook(lambda d: synthetic_screen(d))

    def tearDown(self):
        self.nb.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_a_collections_set_chosen_by_a_word_finds_bs_program(self):
        res = pt.scores(sets="cell_types", select="B_PROG", targets=["A", "B"])
        t = res.table.set_index("target")
        self.assertEqual(set(res.table["score"]), {"B_PROGRAM"})
        self.assertGreater(t.loc["B", "effect"], 0)
        self.assertLess(t.loc["B", "q"], 0.05)
        self.assertGreater(t.loc["B", "effect"], t.loc["A", "effect"])

    def test_a_collection_too_large_is_refused_with_how_to_choose(self):
        with mock.patch.object(pt, "MAX_SETS", 2), self.assertRaisesRegex(ValueError, "give select"):
            pt.scores(sets="cell_types", targets=["A", "B"])

    def test_a_cell_column_and_a_saved_table_are_tested_as_they_are(self):
        table = b_cells_raised(self.nb)
        for given in ("my_value", pt.save_table("values_by_cell", table)):
            res = pt.scores(cell_values=given, targets=["A", "B"])
            t = res.table.set_index("target")
            self.assertLess(t.loc["B", "q"], 0.05, given)
            self.assertGreater(t.loc["B", "effect"], 2, given)
            self.assertLess(abs(t.loc["A", "effect"]), 1, given)

    def test_cell_values_that_miss_cells_are_refused(self):
        table = b_cells_raised(self.nb).iloc[10:]
        with self.assertRaisesRegex(ValueError, "10 of the .* cells read have no value"):
            pt.scores(cell_values=pt.save_table("some_cells", table), targets=["A", "B"])

    def test_c_programs_scores_a_chosen_set_and_a_cell_value(self):
        from pertura.question_modules import REGISTRY
        b_cells_raised(self.nb)
        s = self.nb.screen
        for choices, feature in (({"gene_sets": "cell_types", "select": ["B_PROGRAM"]}, "B_PROGRAM"),
                                 ({"gene_sets": "cell_values", "cell_values": "my_value"}, "my_value")):
            r = REGISTRY["C.programs"].run(s, s.design, os.path.join(self.tmp.name, feature), **choices)
            self.assertEqual(r.status, "detected", r.summary)
            hit = r.table[(r.table["target"] == "B") & (r.table["score"] == feature)]
            self.assertLess(float(hit["q"].iat[0]), 0.05, r.table)


if __name__ == "__main__":
    unittest.main()
