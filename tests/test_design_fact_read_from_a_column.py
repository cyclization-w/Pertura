"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 15), fixed
for any screen (docs/build_goal_session6_fixes.md, item 4): the auditor proposed S3SaSp as batch1, reading part of
the deposit's `batch` column and inferring the rest from capture names, and the form let it through. A fact read from
a column names it, and the check compares it with the column unit by unit."""
import unittest

import pandas as pd

from pertura.design_audit import checks
from pertura.design_audit import inventory as inv


def node(v, **more):
    return {"value": v, "status": "proposed", "source": "the deposit's cell metadata", **more}


def measured():
    meta = pd.DataFrame({"batch": ["batch1"] * 4 + ["batch2"] * 4 + ["batch1"]},
                        index=[f"C{i}" for i in range(9)])
    units = ["S1Sa", "S1Sa", "S2Sp", "S2Sp", "S3SaSp", "S3SaSp", "D1-3", "D1-3", "S3SaSp"]
    return {f["key"]: f for f in inv.columns_by_unit(meta, list(meta.index), units)}


class TestTheColumnByUnit(unittest.TestCase):
    def test_the_measurement_counts_each_units_values(self):
        by = measured()["data.cell_metadata_by_unit"]["value"]["batch"]
        self.assertEqual(by["S1Sa"], {"batch1": 2})
        self.assertEqual(by["S3SaSp"], {"batch2": 2, "batch1": 1})

    def test_a_column_of_many_values_is_left_out(self):
        meta = pd.DataFrame({"umis": range(100)}, index=[f"C{i}" for i in range(100)])
        self.assertEqual(inv.columns_by_unit(meta, list(meta.index), ["u"] * 100), [])


class TestAFactReadFromAColumn(unittest.TestCase):
    def run_check(self, by_unit, column="batch"):
        problems, flags = [], []
        checks._columns({"units": {"batches": node({"by_unit": by_unit}, column=column)}}, measured(), problems, flags)
        return problems, flags

    def test_a_unit_the_column_contradicts_is_a_problem(self):
        problems, _ = self.run_check({"S1Sa": "batch1", "S2Sp": "batch1", "S3SaSp": "batch1", "D1-3": "batch2"})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("S3SaSp is 'batch1' in the proposal, but the column 'batch' gives 'batch2'", problems[0])

    def test_the_columns_own_values_pass(self):
        problems, flags = self.run_check({"S1Sa": "batch1", "S2Sp": "batch1", "S3SaSp": "batch2", "D1-3": "batch2"})
        self.assertEqual(problems, [])
        self.assertTrue(any("S3SaSp's cells hold several values" in f["text"] for f in flags))

    def test_a_column_the_deposit_lacks_is_a_problem(self):
        problems, _ = self.run_check({"S1Sa": "batch1"}, column="run")
        self.assertIn("read from the column 'run'", problems[0])

    def test_the_whole_check_runs_it(self):
        p = {"units": {"batches": node({"by_unit": {"S3SaSp": "batch1"}}, column="batch")}}
        out = checks.check(p, {"facts": list(measured().values())})["problems"]
        self.assertTrue(any("the column 'batch' gives 'batch2'" in x for x in out), out)
        self.assertFalse(any("holds 'column'" in x for x in out), out)


if __name__ == "__main__":
    unittest.main()
