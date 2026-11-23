"""The comparison of two releases' tables, which the stop rule of a fix batch reads: it finds the tables in subfolders
(a module's run writes <module>/result.tsv), and a table sorted another way is not read as changed."""
import importlib.util
import os
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "analysis", "example_report_session6", "compare_tables_between_releases.py")
if os.path.exists(SCRIPT):  # a research script: a release on the analysis server does not carry it
    _spec = importlib.util.spec_from_file_location("compare_tables", SCRIPT)
    compare_tables = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(compare_tables)


@unittest.skipUnless(os.path.exists(SCRIPT), "the research script stays on the laptop")
class TestTheComparison(unittest.TestCase):
    def test_subfolders_and_rows_matched_by_their_keys(self):
        with tempfile.TemporaryDirectory() as d:
            for side, rows in (("a", [("A", 0.01), ("B", 0.5)]), ("b", [("B", 0.5), ("A", 0.9)])):
                os.makedirs(os.path.join(d, side, "B_efficacy"))
                pd.DataFrame(rows, columns=["target", "q"]).to_csv(
                    os.path.join(d, side, "B_efficacy", "result.tsv"), sep="\t", index=False)
            t = compare_tables.compare(os.path.join(d, "a"), os.path.join(d, "b"))
        q = t[(t["table"] == os.path.join("B_efficacy", "result.tsv")) & (t["column"] == "q")]
        self.assertEqual(len(q), 1)
        self.assertEqual(int(q["calls_changed"].iloc[0]), 1)  # A's q moved past 0.05; B's did not, though re-sorted
        self.assertAlmostEqual(float(q["largest_difference"].iloc[0]), 0.89)


if __name__ == "__main__":
    unittest.main()
