"""What development session 9 showed (docs/build/development_sessions/session_09_cd4_rest_activation_gse278572_sonnet,
problems 1-6, 9, 11 and 12): four hashtags marked the four conditions of one pool, the design took hashtags only as
animals or donors, and nothing led the agent to the route the build already had, the hashtags as the units and the
conditions by unit over them. The agent gave the conditions three ways the check refused, then one condition to pass
it. Fixed for any screen whose hashtags mark conditions; the names below are no screen's. Since docs/adr/0041, step
4a, the hashtags are a column the agent writes into the cell tables (hashtags.write_into_cell_tables) and the design
names; the checks of the retired hashtag keys are in tests_research/test_unit_sources_from_a_fixed_list.py."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

from pertura import blocks, design_gate, design_service, hashtags
from pertura.analysis_service import AnalysisError, AnalysisService
from pertura.design_audit import checks
from pertura.design_audit import detectors as dt

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
TAGS = ["HTO_A", "HTO_B", "HTO_C", "HTO_D"]
LANES = {"POOL-1": 500, "POOL-2": 500}


def node(v, status="reported", **more):
    return {"value": v, "status": status, "source": "the user", **more}


def measured():
    """A combined matrix of one pool over two lanes, its four hashtags among surface proteins, as the measurement
    gives them: the cells each feature tops, and the lanes as the candidate units."""
    F = {"data.features.Antibody Capture": {"key": "data.features.Antibody Capture", "value": {
            "n": 6, "role": "co-expressed (surface proteins?)",
            "cells_each_tops": {"HTO_A": 260, "HTO_B": 250, "HTO_C": 240, "HTO_D": 230, "CD3": 15, "CD4": 5}}},
         "units.candidates": {"key": "units.candidates",
                              "value": {"by": "lanes (barcode suffixes of one matrix)", "n": 2, "cells": LANES}}}
    lib = {"samples": ["POOL"], "guides": ["TA_1", "TA_2", "NT_1", "NT_2"],  # the columns as the agent wrote them
           "cell_table_columns": {"hashtag": [""] + TAGS, "lane": list(LANES), "treatment": ["", "drug", "vehicle"],
                                  "time": ["", "24h", "6h"]}}
    return F, lib


def factors():
    """Two treatments at two times, each cell's read from its hashtag into a column of its own."""
    return [{"name": "treatment", "levels": ["vehicle", "drug"], "column": "treatment"},
            {"name": "time", "levels": ["6h", "24h"], "column": "time"}]


def hashtag_units(**more):
    return {"independent_unit": node("none"), "unit_column": node("hashtag"), "pooled_from": node("2 donors"), **more}


def conditions_problems(proposal):
    F, lib = measured()
    problems, flags = [], []
    checks._conditions(proposal, F, lib, problems, flags)
    checks._columns(proposal, F, problems, flags)
    return problems


class TestTheCheckNamesTheRoute(unittest.TestCase):
    def test_conditions_read_from_a_column_the_cell_tables_lack(self):
        # minutes 9.5 and 10.3: a column the agent wrote in its own folder, so not among the cell tables
        p = {"units": {"independent_unit": node("none"), "unit_column": node("lane")},
             "experiment": {"conditions": node({"column": "hashtag_condition", "names": list(TAGS)})}}
        problems = conditions_problems(p)
        missing = [x for x in problems if "no column 'hashtag_condition' in the cell tables" in x]
        self.assertEqual(len(missing), 1, problems)
        self.assertIn("pertura.cell_table.annotate", missing[0])

    def test_a_column_named_in_the_node_says_how_a_column_enters(self):
        p = {"experiment": {"conditions": node([], column="hashtag_condition")}}
        problems = conditions_problems(p)
        self.assertTrue(any("read from the column 'hashtag_condition'" in x and "pertura.cell_table.annotate" in x
                            for x in problems), problems)

    def test_the_hashtags_as_units_with_the_conditions_from_their_columns_pass(self):
        p = {"units": hashtag_units(), "experiment": {"conditions": node(factors())}}
        self.assertEqual(conditions_problems(p), [])
        F, lib = measured()
        problems, flags = [], []
        checks._units(p, F, lib, problems, flags)
        self.assertEqual(problems, [])
        self.assertEqual(design_gate.condition_names(factors()), ["vehicle|6h", "vehicle|24h", "drug|6h", "drug|24h"])

    def test_one_condition_beside_hashtags_is_flagged_for_the_form(self):
        # minute 10.6: one condition ("pooled") passed the check, with the hashtags left aside
        F, lib = measured()
        lib = {**lib, "hashtags": list(TAGS)}
        p = {"units": {"independent_unit": node("none"), "unit_column": node("lane")},
             "experiment": {"conditions": node({"POOL-1": "pooled", "POOL-2": "pooled"})}}
        problems, flags = [], []
        checks._conditions(p, F, lib, problems, flags)
        self.assertEqual(problems, [])
        said = [f["text"] for f in flags if f["key"] == "experiment.conditions"]
        self.assertTrue(any("4 hashtag-like features" in x and "one condition" in x for x in said), flags)
        p["units"] = hashtag_units()
        p["experiment"]["conditions"] = node(factors())
        problems, flags = [], []
        checks._conditions(p, F, lib, problems, flags)
        self.assertFalse(any("hashtag-like" in f["text"] for f in flags), flags)

    def test_a_condition_that_names_a_feature_but_maps_cleanly_is_left_alone(self):
        # a proposal the check accepts is never stopped by a name that is also a feature's
        p = {"units": {"independent_unit": node("none"), "unit_column": node("lane")},
             "experiment": {"conditions": node({"POOL-1": "CD3", "POOL-2": "CD3"})}}
        self.assertEqual(conditions_problems(p), [])


class TestTheReplicatesAndTheUnitsAgree(unittest.TestCase):
    """What session 10 showed: the auditor, revising the replicates against the owner's words, proposed designs the
    check let through: the donors as the replicates with the hashtags (one per condition) as their units, and the
    conditions mapped by unit with no way of telling the units apart."""

    def units_problems(self, units, conditions=None, tags=TAGS):
        F, lib = measured()
        lib["cell_table_columns"]["hashtag"] = list(tags)
        p = {"units": units, "experiment": {"conditions": node(conditions if conditions is not None else factors())}}
        problems, flags = [], []
        checks._units(p, F, lib, problems, flags)
        checks._conditions(p, F, lib, problems, flags)
        return problems

    def test_hashtags_of_each_donor_in_each_condition_pass(self):
        tags = [f"HTO_{d}{c}" for d in "AB" for c in "1234"]  # each donor's cells in each condition
        units = hashtag_units(independent_unit=node("donor"))
        self.assertEqual(self.units_problems(units, factors(), tags), [])

    def test_an_unresolved_column_tells_no_units_apart(self):
        units = {"independent_unit": node("donor"), "unit_column": node("donor", status="unresolved")}
        problems = self.units_problems(units)
        self.assertTrue(any("'donor' is unresolved" in x and "the replicate is none" in x for x in problems), problems)

    def test_conditions_mapped_by_unit_are_refused_with_the_route(self):
        # a mapping over units the build may not have reached no cell; a column holds each cell's condition now
        by_unit = [{"name": "treatment", "levels": ["vehicle", "drug"], "by_unit": {"HTO_A": "vehicle",
                                                                                     "HTO_B": "drug"}}]
        p = {"units": {"independent_unit": node("none"), "pooled_from": node("2 donors")},
             "experiment": {"conditions": node(by_unit)}}
        problems = conditions_problems(p)
        self.assertTrue(any("no column holds them" in x and "pertura.cell_table.annotate" in x for x in problems),
                        problems)


class TestTheWords(unittest.TestCase):
    def test_a_hashtag_marks_a_sample_not_an_animal(self):
        for across in ("one unit", "one unit per sample"):
            self.assertNotIn("animal", hashtags.describe({"method": "htodemux", "across_samples": across}))
        said = " ".join(hashtags.validate({"method": "htodemux", "across_samples": "every lane"}))
        self.assertIn("one condition's cells", said)

    def test_no_word_says_one_screen_per_condition(self):
        self.assertNotIn("one screen", design_gate.REQUIRED["experiment.conditions"])
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(0)
        base = rng.poisson(20, size=(1, 40)).astype(float)
        a, b = base * rng.uniform(0.2, 1.8, size=(1, 40)), base * rng.uniform(0.2, 1.8, size=(1, 40))
        profiles = pd.DataFrame(np.vstack([a * rng.uniform(0.97, 1.03, size=(1, 40)) for _ in range(3)]
                                          + [b * rng.uniform(0.97, 1.03, size=(1, 40)) for _ in range(3)]),
                                index=[f"S{i}" for i in range(6)])
        facts, qs = dt.conditions(None, profiles)
        said = [q for q in qs if q["key"] == "experiment.expression_groups"]
        self.assertEqual(len(said), 1, qs)
        self.assertNotIn("one screen", str(said[0]))
        self.assertIn("analysed apart", str(said[0]))


class TestTheForm(unittest.TestCase):
    def design(self, **units):
        return {"units": hashtag_units(**units), "experiment": {"conditions": node(factors())}}

    def test_no_replicate_told_apart_is_not_no_donors(self):
        # session 10: the owner refused "none told apart" three times, asking for "cannot be identified in this
        # deposit"; the item leads with the owner's fact and its consequence
        said = design_service.form_value(self.design(), "units.independent_unit")
        self.assertTrue(said.startswith("the cells pool 2 donors; they cannot be identified in this deposit"), said)
        self.assertIn("so the analysis counts cells", said)
        self.assertFalse(said.startswith("none"), said)
        self.assertNotIn("no separate mice, donors", said)
        self.assertIn("each cell's unit read from the column 'hashtag'", said)
        self.assertEqual(said.count("2 donors"), 1, said)
        bare = self.design(pooled_from=node(None))
        self.assertIn("no separate mice, donors or cultures", design_service.form_value(bare,
                                                                                         "units.independent_unit"))

    def test_crossed_conditions_by_hashtag_in_words(self):
        said = design_service.form_value(self.design(), "experiment.conditions")
        self.assertEqual(said, "treatment (vehicle, drug) crossed with time (6h, 24h), from the columns 'treatment', "
                               "'time'")

    def test_a_value_the_user_gave_is_not_said_to_be_in_the_files(self):
        self.assertEqual(design_service.origin(self.design(), "units.pooled_from"), "stated in the files or by you")


class TestTheNotebookSaysWhereItMayWrite(unittest.TestCase):
    def test_the_refusal_names_the_other_folders(self):
        with tempfile.TemporaryDirectory() as d:
            own, tables, elsewhere = (os.path.join(d, x) for x in ("notebook", "cell_tables", "deposit"))
            for x in (own, tables, elsewhere):
                os.makedirs(x)
            code = textwrap.dedent(f'''
                import sys
                sys.path.insert(0, {SRC!r})
                from pertura import confine
                confine.install(read=[{elsewhere!r}], write=[{own!r}, {tables!r}], label="notebook code",
                                mode="normal")
                try:
                    open({os.path.join(elsewhere, "x.csv")!r}, "w").write("x")
                except PermissionError as e:
                    print(e)
                ''')
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
            self.assertIn("code writes only in its own folder and in", out.stdout, out.stderr)
            self.assertIn(tables, out.stdout)


class TestGeneSetsNeedOnlyTheOrganism(unittest.TestCase):
    def organism(self, design):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "design.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(design, fh)

            class Service:
                def _design_path(self):
                    return path
            return AnalysisService._organism(Service())

    def test_the_organism_confirmed_while_the_conditions_are_open(self):
        design = {"schema": design_gate.SCHEMA,
                  "experiment": {"organism": node("human", status="confirmed"), "conditions": node([])}}
        self.assertEqual(self.organism(design), "human")

    def test_an_organism_not_confirmed_is_not_read(self):
        with self.assertRaises(AnalysisError):
            self.organism({"schema": design_gate.SCHEMA, "experiment": {"organism": node("human")}})


class TestTheScreenBeforeABuild(unittest.TestCase):
    def test_pt_screen_says_the_object_is_built_after_the_design(self):
        saved = dict(blocks.CONTEXT)
        try:
            with tempfile.TemporaryDirectory() as d:
                blocks.CONTEXT.clear()
                blocks.context(screen_dir=os.path.join(d, "screen_object"))
                with self.assertRaises(ValueError) as e:
                    blocks.screen()
                self.assertIn("no screen object yet", str(e.exception))
                self.assertIn("cell_tables", str(e.exception))
        finally:
            blocks.CONTEXT.clear()
            blocks.CONTEXT.update(saved)


if __name__ == "__main__":
    unittest.main()
