"""A screen of one sample holding several conditions (GSE278572 confirmed as one pool of four hashtag conditions)
chooses tests that read each condition, as a screen of several samples does; and a value a cell column lacks reads as
none through annotate, the build and the screen, as pandas 3 gives it: its astype(str) keeps a missing value as NaN,
where pandas 2 wrote 'nan', so no check for '' or 'nan' caught it (44,500 cells of GSE278572 in a condition 'nan|nan')."""
import tempfile
import unittest

import anndata as ad
import numpy as np
import pandas as pd

from pertura import design_fit as fit
from pertura.finding_checks import Measure
from pertura.question_modules import c as mc

from test_controls_matched_within_each_condition import screen

# the tests that compare a target with control cells of its own samples and conditions, or take the condition as a
# term; the field's tests against every control cell are named so in their labels
MATCHED = {"wilcoxon_same_samples", "fisher_same_samples", "linear_model", "cmh", "propeller", "edger_ql", "pydeseq2",
           "mast"}


def _condition_score(s):
    """A score that condition B raises and target T does not touch (T's cells lean to B)."""
    return np.where(s.condition == "B", 1.0, 0.0) + np.random.default_rng(2).normal(0, 0.5, len(s.label))


class TestOneSampleWithConditions(unittest.TestCase):
    def test_every_lead_and_alternative_reads_the_conditions(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d, units=("U1",))
            self.assertTrue(s.crossed)
            for kind in ("gene", "gene_set", "score", "state"):
                lead = fit.lead(kind, s, "T")
                self.assertIn(lead["method"], MATCHED, (kind, lead))
                self.assertIn("condition", lead["reason"], (kind, lead))
                for other in fit.field_tests(kind, s, "T", lead):
                    self.assertTrue(other["method"] in MATCHED or "every control cell" in other["label"]
                                    or "conditions" in other["label"], (kind, other))
            genes = {o["method"] for o in fit.field_tests("gene", s, "T", {"method": None})}
            scores = {o["method"] for o in fit.field_tests("score", s, "T", {"method": None})}
        self.assertIn("wilcoxon_same_samples", genes)
        self.assertIn("linear_model", scores)

    def test_the_score_tests_take_the_condition_on_one_sample(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d, units=("U1",))
            score = _condition_score(s)
            _, per, _ = mc.compare_scores_field(s, {"x": score}, "control_guides")
            measured = Measure({"kind": "score", "values": pd.Series(score, index=s.adata.obs_names)}, d)(
                s, ["T"], "linear_model")["T"]
            _, p_pooled, _ = mc.score_test(s.unit, score, s.cells_of("T"), s.reference("control_guides", target="T"),
                                           "wilcoxon")
        self.assertEqual(list(per["test"]), ["OLS, condition as a covariate"])
        self.assertGreater(float(per["p"].iat[0]), 0.05)
        self.assertGreater(measured["p"], 0.05)  # the finding's check runs the linear model too
        self.assertLess(p_pooled, 1e-3)  # what the pooled test reads: the condition, as T's

    def test_a_one_sample_screen_of_one_condition_keeps_its_tests(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d, crossed=False, units=("U1",))
            self.assertFalse(s.crossed)
            self.assertEqual(fit.gene_lead(s, "T")["method"], "wilcoxon")
            self.assertEqual(fit.score_lead(s, "T")["method"], "wilcoxon")
            self.assertNotIn("wilcoxon_same_samples",
                             {o["method"] for o in fit.field_tests("gene", s, "T", {"method": None})})


def pandas3_astype(monkeypatch):
    """pandas 3's Series.astype(str) on pandas 2: a missing value stays NaN, where pandas 2 writes 'nan'."""
    real = pd.Series.astype

    def astype(self, dtype, *args, **kwargs):
        out = real(self, dtype, *args, **kwargs)
        if dtype is str:
            missing = self.isna().to_numpy()
            if missing.any():
                out = real(out, object)
                out[missing] = np.nan
        return out

    monkeypatch.setattr(pd.Series, "astype", astype)


def test_pandas3_astype_keeps_nan(monkeypatch):
    pandas3_astype(monkeypatch)
    v = pd.Series(["a", np.nan], dtype="category").astype(str).to_numpy()
    assert v[0] == "a" and isinstance(v[1], float) and np.isnan(v[1])


def test_a_value_a_column_lacks_is_none_through_the_build(tmp_path, monkeypatch):
    """The agent's columns as a dict .map() gives them: a cell its donor table leaves out has no unit, and one
    without a hashtag call has no level of either crossed factor."""
    from pertura import cell_table as ct
    from pertura import screen_object as so
    from pertura.question_modules.data import Screen
    from test_cell_table import _design, _guides, _tables
    from test_design_audit import make_deposit

    pandas3_astype(monkeypatch)
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    tables = _tables(str(dep), str(tmp_path / "cell_tables"))
    donors, stim, kind, lacking = {}, {}, {}, 0
    for smp in ct.samples(tables):
        bcs = list(ad.read_h5ad(smp["matrix"]).obs_names.astype(str))
        n = len(bcs)
        tag = np.array([["", "R_a", "S_a", "R_b", "S_b"][i % 5] for i in range(n)], dtype=object)
        donors[smp["name"]] = pd.Series([f"{smp['name']}-d{i % 2}" for i in range(n - 3)], index=bcs[3:])
        stim[smp["name"]] = pd.Series(tag, index=bcs).map({"R_a": "rest", "S_a": "stim", "R_b": "rest", "S_b": "stim"})
        kind[smp["name"]] = pd.Series(tag, index=bcs).map({"R_a": "a", "S_a": "a", "R_b": "b", "S_b": "b"})
        lacking += int((tag == "").sum())
    r = ct.annotate(tables, "donor", donors)
    assert r["ok"], r
    assert all(v["without_value"] == 3 for v in r["samples"].values()), r["samples"]
    for column, values in (("stim", stim), ("kind", kind)):
        r = ct.annotate(tables, column, values)
        assert r["ok"] and sum(v["without_value"] for v in r["samples"].values()) == lacking, r
        assert "nan" not in r["values"], r["values"]
    design = _design(_guides(str(dep)), independent_unit="donor", unit_column="donor",
                     conditions=[{"name": "stimulation", "levels": ["rest", "stim"], "column": "stim"},
                                 {"name": "cell_type", "levels": ["a", "b"], "column": "kind"}])
    out = so.build(str(dep), design, str(tmp_path / "screen"), cell_tables=tables)
    cells = pd.read_csv(tmp_path / "screen" / "cells.tsv.gz", sep="\t", keep_default_na=False)
    assert "" not in set(cells["unit"]) and (cells["unit"].str.endswith(":unassigned")).sum() == 6
    assert set(cells["condition"]) == {"", "rest|a", "stim|a", "rest|b", "stim|b"}
    assert (cells["condition"] == "").sum() == lacking
    without = [x for x in out["reading_checks"] if x["check"] == "cells without a condition"]
    assert without and without[0]["cells"] == lacking, out["reading_checks"]
    s = Screen(str(tmp_path / "screen"), design)
    assert "" not in s.units() and not any("nan" in str(u) for u in s.units())
    assert set(s.condition) == {"", "rest|a", "stim|a", "rest|b", "stim|b"}


def test_the_reading_check_finds_a_cell_without_one_crossed_level():
    """A build written before the fix: a combination 'nan|nan' with its factors' columns empty."""
    from pertura import reading_checks
    cells = pd.DataFrame({"barcode": [f"S1:c{i}" for i in range(4)], "sample": "S1", "unit": "S1",
                          "label": ["A", "control", "A", "control"], "umis": 100,
                          "condition_stimulation": ["rest", "stim", np.nan, np.nan],
                          "condition_cell_type": ["a", "a", np.nan, np.nan],
                          "condition": ["rest|a", "stim|a", "nan|nan", "nan|nan"]})
    design = {"experiment": {"conditions": {"value": [{"name": "stimulation", "levels": ["rest", "stim"],
                                                       "column": "stim"},
                                                      {"name": "cell_type", "levels": ["a"], "column": "kind"}],
                                            "status": "confirmed"}}}
    out = reading_checks.check(cells, design, {"A_1": ("A", "targeting")})
    without = [x for x in out if x["check"] == "cells without a condition"]
    assert without and without[0]["cells"] == 2, out
