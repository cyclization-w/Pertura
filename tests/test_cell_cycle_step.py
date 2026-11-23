"""Cell cycle (docs/adr/0031, D1a; ADR 0022's routine step): each cell's phase by scanpy's score_genes_cell_cycle or
Seurat's CellCycleScoring with Seurat's gene lists; each target's phase shares by the composition block; C.cell_cycle
as their recipe; the scores as latent variables in Seurat's gene tests."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import envs

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
HAS_R = os.path.exists(envs.rscript())


def cycle_screen(d, organism="human", genes=260):
    """T1's cells have the S genes raised fourfold, so more of them score S; T2 does nothing."""
    from pertura.steps import cell_cycle
    from test_common_response_and_field_measures import build_screen
    s_genes, g2m_genes = cell_cycle.gene_lists(organism)
    groups = {"T1": (80, {g: 4.0 for g in s_genes}), "T2": (80, {}), "CC": (1, {g: 1.0 for g in g2m_genes})}
    design = {"perturbation": {"modality": {"value": "CRISPRi"}}, "units": {"independent_unit": {"value": "none"}},
              "experiment": {"organism": {"value": organism}}}
    s, design = build_screen(d, groups, units=("u1", "u2"), genes=genes, controls=8, control_cells=30, design=design)
    keep = s.label != "CC"
    a = s.adata[keep].copy()
    table = pd.read_csv(os.path.join(d, "guides.tsv"), sep="\t")
    table[table["target"] != "CC"].to_csv(os.path.join(d, "guides.tsv"), sep="\t", index=False)
    from pertura.question_modules.data import Screen
    return Screen(d, design, adata=a), design


class TestTheGeneLists(unittest.TestCase):
    def test_seurats_updated_lists_for_human_and_mouse(self):
        from pertura.steps import cell_cycle
        s, g2m = cell_cycle.gene_lists("human")
        self.assertEqual((len(s), len(g2m)), (43, 54))
        self.assertIn("MCM5", s)
        ms, _ = cell_cycle.gene_lists("mouse")
        self.assertIn("Mcm5", ms)
        with self.assertRaises(ValueError):
            cell_cycle.gene_lists("zebrafish")

    def test_planned_for_human_or_mouse(self):
        from pertura import record as rc
        def planned(org):
            design = {"perturbation": {"modality": {"value": "CRISPRi"}}, "experiment": {"organism": {"value": org}}}
            return {q["question_id"] for q in rc.plan_questions(design, {"n_targets": 5, "guides_per_target": 2})}
        self.assertIn("C.cell_cycle", planned("mouse"))
        self.assertNotIn("C.cell_cycle", planned("zebrafish"))


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestScanpy(unittest.TestCase):
    def test_c_cell_cycle_finds_the_target_with_more_s_cells(self):
        from pertura.question_modules import REGISTRY
        with tempfile.TemporaryDirectory() as d:
            s, design = cycle_screen(d)
            r = REGISTRY["C.cell_cycle"].run(s, design, os.path.join(d, "out"))
        t1 = r.table[(r.table["target"] == "T1") & (r.table["state"] == "S")]
        self.assertGreater(float(t1["share"].iat[0]), float(t1["control_share"].iat[0]))
        self.assertLess(float(t1["q"].iat[0]), 0.05)
        t2 = r.table[(r.table["target"] == "T2") & (r.table["state"] == "S")]
        self.assertGreater(float(t2["q"].iat[0]), 0.05)
        self.assertIn("pt.cell_cycle(method='scanpy')", r.diagnostics["recipe_as_run"])
        self.assertIn("pt.composition(of=cc, column='phase'", r.diagnostics["recipe_as_run"])
        self.assertIn("by_score", r.diagnostics["calibration"])

    def test_a_target_without_the_organism_says_so(self):
        from pertura.question_modules import REGISTRY
        with tempfile.TemporaryDirectory() as d:
            s, design = cycle_screen(d)
            design = {**design, "experiment": {"organism": {"value": "zebrafish"}}}
            r = REGISTRY["C.cell_cycle"].run(s, design, os.path.join(d, "out"))
        self.assertEqual(r.status, "not_enough_data")


@unittest.skipUnless(HAS_SCANPY and HAS_R, "needs scanpy and R with Seurat")
class TestSeurat(unittest.TestCase):
    def test_seurats_cellcyclescoring_agrees_on_the_direction(self):
        """Seurat's module score draws 43 control genes from each of 24 expression bins: 2,000 genes, not 260."""
        from pertura.steps import cell_cycle
        with tempfile.TemporaryDirectory() as d:
            s, _ = cycle_screen(d, genes=2000)
            ph, calls = cell_cycle.phases(s, method="seurat", workdir=os.path.join(d, "r"))
        t1 = (ph["phase"].to_numpy()[s.label == "T1"] == "S").mean()
        ctrl = (ph["phase"].to_numpy()[s.label == "control"] == "S").mean()
        self.assertGreater(t1, ctrl)
        self.assertEqual(calls[0]["package"], "Seurat")

    def test_the_scores_as_latent_variables_in_seuarts_lr(self):
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d:
            s, _ = cycle_screen(d)
            t, calls = genes.compare(s, {"T1": "seurat_lr"}, "control_guides", os.path.join(d, "g"),
                                     params={"cell_cycle": True})
        self.assertIn("S_score", calls[0]["covariates"])
        self.assertTrue(len(t))


if __name__ == "__main__":
    unittest.main()
