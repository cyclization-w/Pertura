"""Development session 4's small fixes (docs/build_goal_adr0032.md, step 1, item 6), each named by its problem in
docs/build/development_sessions/session_04_crisprai_haiku/problems_found_during_the_session.md."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from test_question_modules import synthetic_screen

import pertura.blocks as pt
from pertura.question_modules import REGISTRY
from pertura.question_modules import c as cmod

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


class _Targets:
    """A screen as pt's target check sees it."""

    def targets(self, min_cells):
        return ["A", "B"]


class TestErrorsNameTheRoute(unittest.TestCase):
    def test_a_pairs_label_in_a_single_target_block_names_the_route_for_pairs(self):  # 1h
        with self.assertRaises(ValueError) as e:
            pt._targets(_Targets(), ["A|B"])
        self.assertIn("(C.genes), method seurat3_lr", str(e.exception))
        self.assertIn("regulatory_modes", str(e.exception))
        with self.assertRaises(ValueError) as e:  # a plain missing target keeps its own error
            pt._targets(_Targets(), ["Z"])
        self.assertIn("or more cells here", str(e.exception))

    def test_a_cell_set_used_in_the_cell_that_saved_it_says_when_it_can_be_used(self):  # 1h
        from test_blocks import Notebook
        nb = Notebook(lambda d: synthetic_screen(d))
        try:
            pt.save_cells("first_two", list(np.asarray(nb.screen.adata.obs_names)[:40]), reason="a test")
            with self.assertRaises(ValueError) as e:
                pt._resolve("first_two")
            self.assertIn("from the next cell on", str(e.exception))
            with self.assertRaises(ValueError) as e:  # a set never saved keeps the plain error
                pt._resolve("never_saved")
            self.assertIn("no kept cell set", str(e.exception))
        finally:
            nb.close()

    def test_a_tools_bad_arguments_name_its_arguments(self):  # 1g, 2c
        from pertura.analysis_service import AnalysisService
        with tempfile.TemporaryDirectory() as d:
            svc = AnalysisService({"workspace": d})
            ok, out = svc.call("read_run", {"run_id": "r1", "what": "a reading"})
        self.assertFalse(ok)
        self.assertIn("read_run takes run_id, reading", out["error"])

    def test_read_metadata_names_its_items(self):  # 2c
        from pertura.design_service import TOOLS
        desc = next(t["description"] for t in TOOLS if t["name"] == "read_metadata")
        self.assertIn("geo, guides, object, config or file", desc)


class TestThroughTheDesignService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_design_service import make_deposit, proposal

        from pertura.analysis_service import AnalysisService
        cls.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        cls.svc = AnalysisService({"workspace": os.path.join(cls.tmp.name, "ws")})
        for tool, args in (("open_project", {"data_folder": deposit}), ("measure", {}),
                           ("check_design", {"proposal": proposal()})):
            ok, out = cls.svc.call(tool, args)
            assert ok, out

    @classmethod
    def tearDownClass(cls):
        cls.svc.close()
        cls.tmp.cleanup()

    def test_without_geo_metadata_the_reply_points_to_the_deposits_own_files(self):  # 3
        ok, out = self.svc.call("read_metadata", {"item": "geo"})
        self.assertFalse(ok)
        self.assertNotIn("sample sheet", out["error"])
        self.assertIn("ask the user only for what those do not show", out["error"])

    def test_get_design_gives_the_last_checks_flags_and_problems_with_their_text(self):  # 1c
        ok, out = self.svc.call("get_design", {})
        self.assertTrue(ok, out)
        last = out["last_check"]
        self.assertIsInstance(last["flags"], list)
        self.assertIsInstance(last["problems"], list)
        for f in last["flags"]:
            self.assertTrue(f.get("text"), f)


class TestModulesSayWhatTheyUsed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen, self.design = synthetic_screen(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, name):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d, exist_ok=True)
        return d

    def test_seurat3_lr_takes_the_designs_reference_and_covariate_and_says_so(self):  # 1h-2, 1h-5
        s = self.screen
        r = cmod.genes_seurat3_lr(s, self.out("lr"), groups={"B": ["B"]})
        in_u1 = int(((s.label == "control") & (s.unit == "u1")).sum())
        self.assertEqual(int(r.table.set_index("group").loc["B", "reference cells"]), in_u1)  # B is in u1 only
        self.assertIn("design's default", r.diagnostics["reference"])
        self.assertEqual(r.diagnostics["covariate"], "the sample, as a latent variable")
        self.assertIn("natural log", r.diagnostics["log_base"])
        self.assertIn("natural log", r.summary)
        paper = cmod.genes_seurat3_lr(s, self.out("lr_paper"), groups={"B": ["B"]}, reference_labels=["control"])
        self.assertEqual(int(paper.table.set_index("group").loc["B", "reference cells"]),
                         int((s.label == "control").sum()))  # a paper's stated reference is kept as stated
        self.assertEqual(paper.diagnostics["covariate"], "none")

    def test_regulatory_modes_reads_the_pair_against_reference_pairs(self):  # 1h-2
        rows = []
        rng = np.random.default_rng(0)
        genes = [f"g{i}" for i in range(40)]
        lfc = {"A": rng.normal(0, 1, 40), "B": rng.normal(0, 1, 40), "C": rng.normal(0, 1, 40),
               "D": np.zeros(40)}
        lfc["A|B"] = lfc["A"] + lfc["B"] + np.r_[np.full(10, 2.0), np.zeros(30)]  # departs on ten genes
        lfc["C|D"] = lfc["C"]  # D does not act: the pair is its other single
        for g, v in lfc.items():
            rows += [{"gene": x, "group": g, "avg_logFC": float(y), "DE": bool(abs(y) > 0.5)} for x, y in zip(genes, v)]
        de = pd.DataFrame(rows)
        r = REGISTRY["E.combinations"].fn(self.screen, self.design, self.out("modes"), model="regulatory_modes", de=de,
                                          pair="A|B", singles=["A", "B"],
                                          reference=[{"pair": "C|D", "singles": ["C", "D"]}])
        self.assertIn("reference_band", r.diagnostics)
        self.assertEqual(r.diagnostics["reference_pairs"], 1)
        self.assertIn("Against the reference", r.summary)
        self.assertIn("natural log", r.diagnostics["log_base"])
        self.assertEqual(list(r.tables["reference"]["pair"]), ["C|D"])
        with self.assertRaises(ValueError) as e:
            REGISTRY["E.combinations"].fn(self.screen, self.design, self.out("modes2"), model="regulatory_modes",
                                          de=de, pair="A|B", singles=["A", "B"],
                                          reference=[{"pair": "X|Y", "singles": ["X", "Y"]}])
        self.assertIn("not in the de table", str(e.exception))

    def test_the_de_tables_producer_is_named_in_the_input_and_its_error(self):  # 1h
        from pertura.question_modules.contract import TABLE_PRODUCERS
        self.assertIn("seurat3_lr", REGISTRY["E.combinations"].contract.inputs["de"])
        self.assertIn("seurat3_lr", TABLE_PRODUCERS[("E.combinations", "de")])

    def test_the_strongest_targets_top_genes_lead_by_the_test_not_the_fold_change_alone(self):  # 1i
        changed = pd.DataFrame({
            "target": ["T"] * 4 + ["U"] * 2,
            "gene": ["near_zero", "strong_up", "second_up", "down1", "u1", "u2"],
            "effect": [6.0, 1.2, 0.9, -1.5, 0.8, -0.7],
            "p": [1e-3, 1e-30, 1e-20, 1e-25, 1e-5, 1e-4]})
        summary = pd.DataFrame({"target": ["T", "U"], "genes_changed": [4, 2]})
        tops = cmod.top_genes(changed, summary)
        self.assertEqual(tops["T"]["up"], ["strong_up", "second_up", "near_zero"])
        self.assertEqual(tops["T"]["down"], ["down1"])
        self.assertEqual(list(tops), ["T", "U"])
        words = cmod.top_genes_words(tops)
        self.assertIn("T (up strong_up, second_up, near_zero; down down1)", words)

    def test_a_control_line_from_few_groups_says_what_it_means(self):  # 1g
        self.assertIn("always past it", cmod.few_groups_words(4))
        self.assertEqual(cmod.few_groups_words(40), "")

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_pair_fits_two_measures_are_named_apart(self):  # 1h-5
        s = self.screen
        label = s.label.copy()
        s.label = np.where((label == "A") & (s.top_guide == "A_1"), "A|C", label)
        try:
            r = REGISTRY["E.combinations"].fn(s, self.design, self.out("pairs"), min_cells=10, gene_sets="given",
                                              gene_lists={"S": [f"s{i}" for i in range(10)]})
        finally:
            s.label = label
        self.assertNotIn("additive_R2", r.table.columns)  # the shape measure is named apart from the fit's R²
        self.assertIn("additive_correlation_sq", r.table.columns)
        self.assertIn("ignores scale", REGISTRY["E.combinations"].contract.returns)


if __name__ == "__main__":
    unittest.main()
