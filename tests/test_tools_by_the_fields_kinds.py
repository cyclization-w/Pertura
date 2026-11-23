"""The analyses by the field's kinds (docs/adr/0044, D2 and D3): eleven tools start the modules, a view picking one,
and each takes the same slice of the cells:
- every module but the first look's (A) is started by a view, and each tool's description names its views' modules
  and choices from their contracts;
- against is the module's reference where it takes one, and refused where the module reads its own way; targets is
  the module's input where it takes one, else the other targets' cells are left out of the reading, as the blocks
  leave them; a view's presets are defaults that the call's own choices override;
- a tool's own parameters (the options the notebooks needed) carry their contracts' words, reach their modules'
  choices and inputs, and bring the one choice value an input goes with, unless the call chose it;
- cells given as {column: value}, or as a state of a states run, are kept as a cell set, once;
- a run's notebook lines name its slice, and a result's reply gives them;
- the module runner reads only the targets asked, the reference kept;
- where the agent plans, the routes of published screens and the kinds of conclusion name the tool calls that run
  their steps, and a card's step that a tool's call does names that call, checked against the tool."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

from synthetic_screen import make_cell_screen
from test_design_service import make_deposit

from pertura import analysis_service as asv
from pertura import blocks as pt
from pertura import kinds
from pertura.analysis_service import AnalysisError, AnalysisService
from pertura.question_modules import REGISTRY
from pertura.question_modules.__main__ import _run
from pertura.question_modules.c import any_effect_recipe, genes_recipe
from pertura.question_modules.contract import Contract, Result, register


class TestTheTools(unittest.TestCase):
    def test_every_module_but_the_first_looks_is_started_by_a_view(self):
        started = {m for _, views in kinds.KINDS.values() for m, _ in views.values()}
        self.assertEqual(started, {m for m in REGISTRY if m[0] in "BCDEFG"})
        for name, (_, views) in kinds.KINDS.items():
            entry = kinds.tool(name)
            self.assertEqual(entry["inputSchema"]["properties"]["view"]["enum"], list(views))
            for view, (module, _) in views.items():
                self.assertIn(f"{view} ({module}", entry["description"])
        self.assertIn("method mixscape|mixscale", kinds.description("guides"))
        self.assertNotIn("cache", kinds.description("guides"))  # an input kept inside one process
        self.assertEqual(kinds.kind_of("C.programs"), ("scores", "scores"))  # the view without presets
        self.assertEqual(kinds.kind_of("C.any_effect"), ("impact", "distance"))

    def test_each_tools_own_parameters_carry_their_contracts_words(self):
        for name, own in kinds.PROMOTED.items():
            props, views = kinds.tool(name)["inputSchema"]["properties"], kinds.description(name).split("Views:")[1]
            for k in own:
                self.assertTrue(props[k]["description"], (name, k))
                self.assertNotRegex(views, rf"[ ,;]{k}[,);]", (name, k))  # not listed again among a view's others
        scores = kinds.tool("scores")["inputSchema"]["properties"]
        self.assertEqual(scores["gene_sets"]["enum"], REGISTRY["C.programs"].contract.choices["gene_sets"])
        self.assertIn("{column, levels: [a, b], top}", scores["contrast"]["description"])
        self.assertIn("(view states)",
                      kinds.tool("states")["inputSchema"]["properties"]["trajectory_root"]["description"])

    def test_the_status_files_list_is_the_same(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin",
                                        "status"))
        import status_file
        self.assertEqual(status_file.KINDS, tuple(kinds.KINDS))
        self.assertTrue(set(status_file.RENAMED) <= {t["name"] for t in AnalysisService.TOOLS})
        self.assertTrue(set(status_file.RENAMED.values()) <= set(asv.RETIRED))


class TestTheTextsWhereTheAgentPlans(unittest.TestCase):
    MODULE = r"\b[A-H]\.[a-z_]{3,}"

    def test_the_routes_and_the_kinds_of_conclusion_name_the_tool_calls(self):
        import re
        from pertura import candidates, moves, paradigms
        cards = paradigms.load(None, modules=set(REGISTRY))
        every = {"modes": ["crispri", "crispra"], "n_control_guides": 20, "pairs": True, "time_course": True,
                 "n_conditions": 2}
        chains = paradigms.chains(cards, every)
        self.assertEqual(len(chains), len(cards))
        for chain in chains:  # each step's part, in brackets, as the agent runs it
            for part in re.findall(r"\[([^\[\]]*)\](?= \d+\.|$)", chain):
                self.assertNotRegex(part, self.MODULE, chain[:40])
        self.assertIn("[scores with contrast]", chains[1])
        self.assertIn("[states with trajectory_root]", chains[2])
        for how in [h for _, h in candidates.KINDS.values()] + list(candidates.BLOCK_ROUTES.values()):
            self.assertNotRegex(how, self.MODULE)
        for line in moves.after("C.genes", set(cards)) + moves.after("E.similar_targets", set(cards)):
            self.assertNotRegex(re.sub(r"\([A-H]\.[a-z_]+[^()]*\)", "", line), self.MODULE, line)  # a module only after its call

    def test_a_tool_part_is_a_call_there_is(self):
        from pertura import paradigms
        card = {"id": "P9", "version": 1, "updated": "2026-11-09", "name": "x", "use_when": "x", "leads": "x",
                "steps": [{"id": "P9.1", "does": "a", "part": "tool:scores contrast"},
                          {"id": "P9.2", "does": "b", "part": "tool:states view within"},
                          {"id": "P9.3", "does": "c", "part": "tool:genes genes=targets_own"}]}
        self.assertEqual(paradigms.problems(card, set(REGISTRY)), [])
        self.assertEqual([paradigms.called(s["part"]) for s in card["steps"]],
                         ["scores with contrast", "states view within", "genes with genes=targets_own"])
        self.assertEqual([paradigms.module_of(s["part"]) for s in card["steps"]],
                         ["C.programs", "C.within_states", "C.genes"])
        card["steps"] = [{"id": "P9.1", "does": "a", "part": "tool:scorez"},
                         {"id": "P9.2", "does": "b", "part": "tool:states view across"},
                         {"id": "P9.3", "does": "c", "part": "tool:states contrast"}]
        self.assertEqual(paradigms.problems(card, set(REGISTRY)),
                         ["step P9.1: no tool 'scorez'", "step P9.2: states has no view across",
                          "step P9.3: states takes no contrast"])


class TestTheSlice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        for name in ("run_module", "_targets_known"):
            patcher = mock.patch.object(AnalysisService, name, return_value={"run_id": "r1"})
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def started(self):
        c = self.run_module.call_args
        return c.args[:3], c.kwargs

    def test_against_and_targets_reach_the_module_as_it_takes_them(self):
        self.svc.impact(against="other_targets")
        self.assertEqual(self.started()[0], ("C.any_effect", {"reference": "other_targets"}, None))
        self.svc.genes(targets=["A"])  # C.genes takes targets
        args, kw = self.started()
        self.assertEqual((args, kw["targets"]), (("C.genes", None, {"targets": ["A"]}), None))
        self.svc.impact(targets=["A", "B"])  # C.any_effect does not: the runner leaves the others' cells out
        args, kw = self.started()
        self.assertEqual((args, kw["targets"]), (("C.any_effect", None, None), ["A", "B"]))
        self.svc.similarity(against="control_guides")  # the default meaning, said or not
        out = self.svc.states(view="within", by="condition")
        self.assertEqual((self.started()[0][0], self.started()[1]["by"]), ("C.within_states", "condition"))
        self.assertEqual(out["analysis"], "states, view within: C.within_states")
        with self.assertRaisesRegex(AnalysisError, "without against"):
            self.svc.similarity(against="other_targets")
        with self.assertRaisesRegex(AnalysisError, "every target against the others"):
            self.svc.impact(against="other_targets", targets=["A"])
        with self.assertRaisesRegex(AnalysisError, "no view 'nowhere'"):
            self.svc.states(view="nowhere")

    def test_a_views_presets_are_defaults(self):
        self.svc.programs()
        self.assertEqual(self.started()[0], ("C.programs", {"gene_sets": "cnmf"}, None))
        self.svc.programs(choices={"gene_sets": "sklearn_nmf"})
        self.assertEqual(self.started()[0], ("C.programs", {"gene_sets": "sklearn_nmf"}, None))
        self.svc.reproducibility(view="fewer_cells")
        self.assertEqual(self.started()[0], ("C.any_effect", None, {"fewer_cells": [25, 50, 100, 200]}))

    def test_a_tools_own_parameters_reach_the_module_and_bring_the_choice_they_go_with(self):
        self.svc.genes(genes=["IFIT1", "ISG15"])
        self.assertEqual(self.started()[0], ("C.genes", None, {"genes": ["IFIT1", "ISG15"]}))
        contrast = {"column": "condition", "levels": ["rest", "stim"]}
        self.svc.scores(contrast=contrast)
        self.assertEqual(self.started()[0], ("C.programs", {"gene_sets": "contrast"}, {"contrast": contrast}))
        self.svc.scores(gene_sets="hallmark", select=["HALLMARK_TNFA_SIGNALING_VIA_NFKB"])
        self.assertEqual(self.started()[0], ("C.programs", {"gene_sets": "hallmark"},
                                             {"select": ["HALLMARK_TNFA_SIGNALING_VIA_NFKB"]}))
        self.svc.scores(gene_sets="given", contrast=contrast)  # the call's choice stays; the module refuses the pair
        self.assertEqual(self.started()[0][1], {"gene_sets": "given"})
        self.svc.enrichment(de="r0")  # each target's changed genes, the module's default method (ora)
        self.assertEqual(self.started()[0], ("G.meaning", None, {"de": "r0"}))
        self.svc.conditions(genes=["IFIT1"], pair=["rest", "stim"])
        self.assertEqual(self.started()[0], ("E.condition_x_perturbation", {"what": "genes"},
                                             {"genes": ["IFIT1"], "pair": ["rest", "stim"]}))
        root = {"condition": "rest"}
        self.svc.states(trajectory_root=root)
        self.assertEqual(self.started()[0], ("C.states_and_composition", None, {"trajectory_root": root}))
        self.svc.guides(view="escape", inputs={"n_pcs": 20})  # an input in inputs brings no choice: the module checks
        self.assertEqual(self.started()[0], ("B.escape", None, {"n_pcs": 20}))
        with self.assertRaisesRegex(AnalysisError, "takes no trajectory_root; view states does"):
            self.svc.states(view="within", trajectory_root="S01")
        with self.assertRaisesRegex(AnalysisError, "genes has no parameter"):
            self.svc.genes(nope=1)
        with self.assertRaisesRegex(AnalysisError, "programs has no parameter"):
            self.svc.programs(gene_lists={"x": ["A"]})
        ok, out = self.svc.call("scores", {"contrast": contrast})  # as the client calls it
        self.assertTrue(ok, out)
        self.assertEqual(self.started()[0][1], {"gene_sets": "contrast"})

    def test_through_the_service_a_tool_is_a_call_like_any_other(self):
        ok, out = self.svc.call("scores", {"view": "scores", "choices": {"gene_sets": "hallmark"}})
        self.assertTrue(ok, out)
        self.assertEqual(self.started()[0], ("C.programs", {"gene_sets": "hallmark"}, None))
        ok, out = self.svc.call("scores", {"no_such": 1})
        self.assertIn("scores has no parameter ['no_such']; its own are gene_sets", out["error"])


class TestCellsByAColumn(unittest.TestCase):
    def test_kept_as_a_set_once_and_refused_where_no_cell_has_it(self):
        with tempfile.TemporaryDirectory() as d:
            deposit, screen = os.path.join(d, "deposit"), os.path.join(d, "screen")
            os.makedirs(deposit)
            os.makedirs(screen)
            make_deposit(deposit)
            pd.DataFrame({"barcode": [f"c{i}" for i in range(6)], "sample": "s1", "unit": "s1",
                          "label": ["A", "B", "control"] * 2, "guides": "",
                          "condition": ["rest"] * 3 + ["stim"] * 3}).to_csv(
                os.path.join(screen, "cells.tsv.gz"), sep="\t", index=False)
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                ok, out = svc.call("open_project", {"data_folder": deposit})
                self.assertTrue(ok, out)
                with mock.patch.object(AnalysisService, "_screen_dir", return_value=screen):
                    name = svc._kept_slice({"condition": "stim"})
                    self.assertTrue(name.startswith("condition_stim-"), name)
                    self.assertEqual(svc.record.state()["cell_sets"][name]["cells"], 3)
                    self.assertEqual(svc._kept_slice({"condition": ["stim"]}), name)  # the same cells: kept once
                    with self.assertRaisesRegex(AnalysisError, "no cell column 'nowhere'"):
                        svc._kept_slice({"nowhere": "x"})
                    with self.assertRaisesRegex(AnalysisError, "no cell has"):
                        svc._kept_slice({"condition": "never"})
                    states = pd.DataFrame({"barcode": [f"c{i}" for i in range(6)], "state": ["S01", "S02"] * 3,
                                           "state_name": ["resting", "myCAF"] * 3})
                    states.attrs["run"] = "r7"
                    with mock.patch.object(AnalysisService, "_states_of", return_value=states):
                        name = svc._kept_slice({"state": "S02"})
                        kept = svc.record.state()["cell_sets"][name]
                        self.assertEqual(kept["cells"], 3)
                        self.assertIn("the states of run r7", kept["reason"])
                        name = svc._kept_slice({"state_name": "myCAF", "condition": "stim"})  # by its name, in one
                        self.assertEqual(svc.record.state()["cell_sets"][name]["cells"], 2)  # condition
                with mock.patch.object(AnalysisService, "_module_runs", return_value={}):
                    with self.assertRaisesRegex(AnalysisError, "no states run has ended"):
                        svc._states_of()
                self.assertEqual(svc._kept_slice("a_kept_set"), "a_kept_set")
            finally:
                svc.close()


class TestTheNotebooksLines(unittest.TestCase):
    def test_a_runs_lines_name_its_slice_and_its_reply_gives_them(self):
        self.addCleanup(pt.SLICE.clear)
        pt.SLICE.update(cells="condition_stim-1a2b3c4d", targets=["A"])
        code = pt.recipe_as_run(any_effect_recipe)
        self.assertIn("cells='condition_stim-1a2b3c4d'", code)
        self.assertIn("targets=['A']", code)
        self.assertIn("pt.calibrate(res)", code)  # it takes no slice
        code = pt.recipe_as_run(genes_recipe, targets=["B"])  # a module's own targets stay
        self.assertIn("targets=['B']", code)
        self.assertNotIn("'A'", code)
        out = asv._concise({"run_id": "r1", "diagnostics": {"recipe_as_run": "res = pt.genes()",
                                                            "package_calls": ["x" * 400]}})
        self.assertEqual(out["in_the_notebook"], "res = pt.genes()")
        self.assertNotIn("recipe_as_run", out["diagnostics"])
        self.assertEqual(out["in_result_json"], ["package_calls"])


class TestTheRunnersTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen, _, _ = make_cell_screen(self.tmp.name, seed=3)

        @register("T.labels_read", Contract(serves="T.labels_read", applies_when="always", needs="labels",
                                            returns="the labels a module reads",
                                            fails_when=[("a count is no test", "none: a test module")],
                                            tested_against="this test"))
        def read(screen, design, out_dir):
            table = pd.Series(screen.label).value_counts().rename_axis("label").reset_index(name="cells")
            path = os.path.join(out_dir, "figure.png")
            open(path, "w").close()
            return Result("measured", table, {}, path, {}, f"{len(screen.label)} cells")

    def tearDown(self):
        del REGISTRY["T.labels_read"]
        self.tmp.cleanup()

    def test_only_the_targets_asked_are_read_and_the_reference_stays(self):
        out = os.path.join(self.tmp.name, "out")
        os.makedirs(out)
        with mock.patch("pertura.question_modules.__main__.Screen", return_value=self.screen):
            r = _run("T.labels_read", self.tmp.name, None, out, targets=["EVERY"])
        cells = dict(zip(r.table["label"], r.table["cells"]))
        self.assertEqual(set(cells) - {"none"}, {"EVERY", "control", "excluded"})
        self.assertEqual(cells["control"], int((self.screen.label == "control").sum()))
        self.assertEqual(pt.SLICE, {})  # cleared after the run

    def test_a_run_with_no_target_to_test_says_so_and_has_not_failed(self):
        """GSE263761's replay: every cell 'multi', and the survey's B.efficacy and C.genes runs failed on NoCells."""
        @register("T.no_cells", Contract(serves="T.no_cells", applies_when="always", needs="labels",
                                         returns="nothing", fails_when=[("none", "none")], tested_against="this test"))
        def none(screen, design, out_dir):
            raise pt.NoCells("no target with 10 or more cells here")
        self.addCleanup(REGISTRY.pop, "T.no_cells")
        out = os.path.join(self.tmp.name, "out_none")
        with mock.patch("pertura.question_modules.__main__.Screen", return_value=self.screen):
            r = _run("T.no_cells", self.tmp.name, None, out)
        self.assertEqual((r.status, r.summary), ("not_enough_data", "no target with 10 or more cells here"))
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "not_enough_data")


if __name__ == "__main__":
    unittest.main()
