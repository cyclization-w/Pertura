"""What the CRISPRai development session (docs/build/development_sessions/session_01_crisprai) showed, fixed for any
screen: the design check and the build refuse or flag what they cannot follow instead of falling back quietly (a unit
line in words, a condition that reaches no cell), the organism is asked for, a notebook cell in flight does not break
other calls, an unexpected failure is named, a module with nothing to measure says so, and the report says what was
run, on which cells, and which findings it does not show."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import record as rc, results_report, screen_object
from pertura.analysis_service import AnalysisService
from pertura.design_audit import checks


def node(v):
    return {"value": v, "status": "proposed", "source": "test"}


def unit_proposal(col=None, unit="none"):
    p = {"units": {"independent_unit": node(unit)}}
    if col:
        p["units"]["unit_column"] = node(col)
    return p


class TestTheUnitLine(unittest.TestCase):
    F = {"data.cell_metadata": {"value": {"columns": ["cell", "orig.ident", "batch"]}}}

    def problems(self, proposal, F=None, lib=None):
        problems, flags = [], []
        checks._units(proposal, F or self.F, lib or {}, problems, flags)
        return problems

    def test_a_unit_line_in_words_is_refused_even_without_replicates(self):
        """Session 1: 'independent_unit: none' skipped the check, and a description in words made the build put
        every cell in one unit. Since docs/adr/0041, step 4a, a column holds the units, and a way in words in its
        place is refused."""
        p = unit_proposal("orig.ident")
        p["units"]["unit_identification"] = node("10x capture, from the barcode prefix")
        out = self.problems(p)
        self.assertTrue(any("not part of the design any more" in x for x in out), out)

    def test_a_column_of_the_deposits_cell_metadata_is_a_unit_column(self):
        self.assertEqual(self.problems(unit_proposal("orig.ident")), [])
        out = self.problems(unit_proposal("capture"))
        self.assertTrue(any("no column 'capture'" in p for p in out), out)

    def test_no_unit_and_no_column_given_is_fine(self):
        self.assertEqual(self.problems(unit_proposal()), [])

    def test_the_build_refuses_a_way_it_cannot_follow(self):
        design = {"units": {"independent_unit": {"value": "none"},
                            "unit_identification": {"value": "10x capture, from the barcode prefix"}}}
        with self.assertRaises(ValueError) as e:
            screen_object._unit_column(design)
        self.assertIn("no longer reads", str(e.exception))
        column = {"units": {"independent_unit": {"value": "none"}, "unit_column": {"value": "orig.ident"}}}
        self.assertEqual(screen_object._unit_column(column), "orig.ident")
        self.assertIsNone(screen_object._unit_column({"units": {"independent_unit": {"value": "none"}}}))


class TestAConditionMustReachItsCells(unittest.TestCase):
    cells = pd.DataFrame({"sample": ["RNA"] * 4, "unit": ["S1Sa", "S1Sa", "S2Sp", "D1"]})

    def test_a_condition_that_reaches_no_cell_stops_the_build(self):
        """Session 1: keys 'S1Sa_' against units 'RNA'; every cell's condition was empty and nothing said so. Each
        cell's condition is read from a column now (docs/adr/0041, step 4b): one that holds no value stops the
        build."""
        factors = [{"name": "line", "by": "column", "map": "line"}]
        with self.assertRaises(ValueError) as e:
            screen_object.condition_reach(factors, [[""] * 4], self.cells)
        self.assertIn("reaches no cell", str(e.exception))
        self.assertIn("'line'", str(e.exception))

    def test_a_condition_that_reaches_some_cells_is_returned_for_a_flag(self):
        factors = [{"name": "line", "by": "column", "map": "line"}]
        out = screen_object.condition_reach(factors, [["a", "a", "i", ""]], self.cells)
        self.assertEqual(out, {"line": "3 of 4"})

    def test_a_named_condition_and_a_missing_column_are_left_to_their_own_flags(self):
        factors = [{"name": "named", "by": None, "map": None}, {"name": "col", "by": "column", "map": "dose"}]
        self.assertEqual(screen_object.condition_reach(factors, [[""] * 4, [""] * 4], self.cells, {"dose"}), {})


class TestTheOrganism(unittest.TestCase):
    human = [f"GENE{i}" for i in range(600)]
    mouse = [f"Gene{i}" for i in range(600)]

    def test_gene_names_read_as_an_organism(self):
        self.assertEqual(checks.organism_of_genes(self.human), "human")
        self.assertEqual(checks.organism_of_genes(self.mouse), "mouse")
        self.assertEqual(checks.organism_of_genes([f"ENSMUSG{i:011d}" for i in range(600)]), "mouse")
        self.assertIsNone(checks.organism_of_genes([f"g{i}" for i in range(600)]))  # the synthetic screens
        self.assertIsNone(checks.organism_of_genes(self.human[:100]))  # too few to tell

    def test_the_check_asks_for_the_organism_and_flags_a_disagreement(self):
        """Session 1: no organism in the design, so the gene sets refused and the agent read them as missing."""
        for proposal, lib, want_problem, want_flag in (
                ({}, {"genes": self.human}, True, False),
                ({"experiment": {"organism": node("human")}}, {"genes": self.human}, False, False),
                ({"experiment": {"organism": node("mouse")}}, {"genes": self.human}, False, True),
                ({"experiment": {"organism": node("martian")}}, {"genes": self.human}, True, False),
                ({}, {"genes": [f"g{i}" for i in range(600)]}, False, False)):
            problems, flags = [], []
            checks._organism(proposal, lib, problems, flags)
            self.assertEqual(bool(problems), want_problem, (proposal, problems))
            self.assertEqual(bool(flags), want_flag, (proposal, flags))


class TestCallsBesideANotebookCell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_notebook_cell_in_flight_is_not_looked_up_as_a_job(self):
        """Session 1: run_code's run is 'running' without a job while its cell runs; sync_runs looked for its job
        folder and the KeyError reached the client as 'Internal error'."""
        r = rc.Record(os.path.join(self.tmp.name, "record"))
        r.append("run_logged", {"run_id": "run0001-abc", "what": "a cell", "code": "x = 1", "inputs": []}, rc.TOOL)
        jobs = os.path.join(self.tmp.name, "jobs")
        os.makedirs(jobs)
        self.assertEqual(r.sync_runs(jobs), [])
        self.assertEqual(r.state()["runs"]["run0001-abc"]["state"], "running")

    def test_an_unexpected_failure_is_named_to_the_agent(self):
        svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        svc.pertura_status = lambda: 1 / 0
        ok, out = svc.call("pertura_status", {})
        self.assertFalse(ok)
        self.assertTrue(out["error"].startswith("Pertura failed inside pertura_status: ZeroDivisionError"), out)
        self.assertIn("read_record", out["note"])


class TestAModuleWithNothingToMeasure(unittest.TestCase):
    def test_efficacy_says_no_target_is_a_gene_instead_of_crashing(self):
        """Session 1: target names such as GATA1-a are not genes of the matrix; B.efficacy crashed drawing a log
        axis with no point on it."""
        from synthetic_screen import make_cell_screen
        from pertura import question_modules as qm
        with tempfile.TemporaryDirectory() as d:
            screen, design, _ = make_cell_screen(d)
            design = {**design, "perturbation": {**design.get("perturbation", {}),
                                                 "modality": {"value": "CRISPRi"}}}
            r = qm.REGISTRY["B.efficacy"].run(screen, design, os.path.join(d, "out"))
        self.assertEqual(r.status, "not_enough_data")
        self.assertIn("is a gene of the matrix", r.summary)


class TestTheReportSaysWhatRan(unittest.TestCase):
    def test_a_module_run_on_a_cell_set_says_so(self):
        module, how = results_report._module('module C.any_effect with {"cells": "S1Sa", "choices": '
                                              '{"reference": "other_targets"}}')
        self.assertEqual(module, "C.any_effect")
        self.assertEqual(how, "reference other_targets, on the cell set S1Sa")

    def test_a_finding_in_a_few_words(self):
        f = {"target": "KLF1-a", "feature": "heme metabolism", "direction": "up", "cells": "S1Sa"}
        self.assertEqual(results_report._finding_line(f), "KLF1-a raises heme metabolism (cells: S1Sa)")


class TestADossierProposedAgain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = rc.Record(os.path.join(self.tmp.name, "record"))
        self.r.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                           "because": "always"}, rc.PLAN)
        for rid in ("r1", "r2"):
            self.r.append("run_logged", {"run_id": rid, "what": "module C.genes", "code": "", "inputs": [],
                                         "question_id": "C.genes"}, rc.TOOL)
        self.r.append("finding_recorded", {"finding_id": "f1", "question_id": "C.genes", "target": "TA",
                                           "feature": "G1", "direction": "down", "population": "all",
                                           "run_ids": ["r1"]}, rc.AGENT)

    def tearDown(self):
        self.tmp.cleanup()

    def propose(self, main_run, size=None):
        self.r.append("dossier_proposed", {"dossier_id": "d1", "title": "t", "lead_findings": ["f1"],
                                           "main_run": main_run, "map_size": size or {"targets": 8, "genes": 0}},
                      rc.AGENT)

    def test_a_dossier_can_take_another_main_run_until_its_panel_is_made(self):
        """Session 1: the agent's own figure could not lead its result, because the dossier named the module's run
        and could not be proposed again."""
        self.propose("r1")
        self.r.append("dossier_part_recorded", {"dossier_id": "d1", "part": "effect", "text": "x",
                                                "run_ids": ["r1"]}, rc.AGENT)
        self.propose("r2")
        d = self.r.state()["dossiers"]["d1"]
        self.assertEqual(d["main_run"], "r2")
        self.assertIn("effect", d["parts"])  # the parts recorded so far are kept
        self.r.append("dossier_part_recorded", {"dossier_id": "d1", "part": "panel_and_paragraph", "text": "x",
                                                "figure": "panel.png"}, rc.AGENT)
        with self.assertRaises(rc.RecordError):
            self.propose("r1")

    def test_a_mechanism_part_may_claim_none_for_its_findings(self):
        # development session 3, problem 26: the part demanded knowledge, so the agent cited gene-set definitions
        self.propose("r1")
        self.r.append("dossier_part_recorded", {"dossier_id": "d1", "part": "mechanism",
                                                "text": "No mechanism is claimed", "finding_ids": ["f1"]}, rc.AGENT)
        self.assertIn("mechanism", self.r.state()["dossiers"]["d1"]["parts"])
        with self.assertRaises(rc.RecordError) as e:
            self.r.append("dossier_part_recorded", {"dossier_id": "d1", "part": "mechanism", "text": "x"}, rc.AGENT)
        self.assertIn("finding_ids", str(e.exception))

    def test_the_agents_own_figure_is_not_listed_as_a_small_map(self):
        self.propose("r1", {"targets": None, "genes": None, "measured": False})
        self.r.append("dossier_chosen", {"dossier_ids": ["d1"]}, rc.USER)
        kinds = [i["kind"] for i in rc.dossier_items(self.r.state())]
        self.assertNotIn("result's main figure covers few targets or genes", kinds)


if __name__ == "__main__":
    unittest.main()
