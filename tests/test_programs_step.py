"""The programs step (docs/adr/0031, D1a): programs found in the cells by the field's packages behind one interface,
pt.programs for the notebook and C.programs as its recipe.
- Each method finds a planted program (20 genes raised together in some cells), and pt.scores(programs=...) reads
  the target that carries it more often.
- fit_on controls finds the program in the control cells and reads every cell on it.
- C.programs is its recipe: the module's table is pt.scores' and the calibration on control-guide groups is told."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import envs

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None
HAS_R = os.path.exists(envs.rscript())
HAS_CNMF = os.path.exists(envs.cnmf_python())
PROGRAM = [f"p{i}" for i in range(20)]


def program_screen(d, units=("u1", "u2", "u3"), share_control=0.3, share_target=0.8, seed=0):
    """Targets T1 and T2 and 12 control guides in each unit. The program's 20 genes are raised fourfold in a share
    of the cells: share_control of the control and T2 cells, share_target of T1's."""
    from test_common_response_and_field_measures import build_screen
    groups = {"T1": (80, {}), "T2": (80, {}), "PROG": (1, {g: 1.0 for g in PROGRAM})}
    s, design = build_screen(d, groups, units=units, genes=200, controls=12, control_cells=20, seed=seed)
    keep = s.label != "PROG"
    a = s.adata[keep].copy()
    rng = np.random.default_rng(seed + 1)
    share = np.where(a.obs["label"].to_numpy() == "T1", share_target, share_control)
    on = rng.random(a.n_obs) < share
    X = a.X.tolil()
    cols = [list(a.var_names).index(g) for g in PROGRAM]
    for i in np.flatnonzero(on):
        for j in cols:
            X[i, j] = round(X[i, j] * 4) + 2
    a.X = X.tocsr()
    table = pd.read_csv(os.path.join(d, "guides.tsv"), sep="\t")
    table[table["target"] != "PROG"].to_csv(os.path.join(d, "guides.tsv"), sep="\t", index=False)
    from pertura.question_modules.data import Screen
    return Screen(d, design, adata=a), design


def program_of_the_planted_genes(genes, top=20):
    """The program whose top genes hold most of the planted genes, and how many."""
    best, n = None, -1
    for p, g in genes.groupby("program"):
        hit = len(set(g.sort_values("gene_rank")["gene"].head(top)) & set(PROGRAM))
        if hit > n:
            best, n = p, hit
    return best, n


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def found(self, method, fit_on="all", k=None, units=("u1", "u2", "u3"), **params):
        from pertura.steps import programs
        s, _ = program_screen(self.d, units=units)
        usage, genes, about, calls = programs.discover(s, method, os.path.join(self.d, method), k=k, fit_on=fit_on,
                                                       **params)
        p, n = program_of_the_planted_genes(genes)
        self.assertGreaterEqual(n, 12, genes[genes["program"] == p])  # most of the planted genes lead one program
        t1 = usage.loc[np.asarray(s.adata.obs_names)[s.label == "T1"], p].mean()
        ctrl = usage.loc[np.asarray(s.adata.obs_names)[s.label == "control"], p].mean()
        self.assertGreater(t1, ctrl)  # T1 carries the program more often
        self.assertEqual(about["fit_on"], fit_on)
        self.assertEqual(len(usage), len(s.label))
        self.assertTrue(calls and calls[0]["package"])
        return s, usage, genes, about


@unittest.skipUnless(HAS_SCANPY and HAS_SKLEARN, "needs scanpy and scikit-learn")
class TestSklearnNMF(Base):
    def test_finds_the_planted_program(self):
        self.found("sklearn_nmf", k=4)

    def test_found_in_the_control_cells_and_every_cell_read_on_it(self):
        _, _, _, about = self.found("sklearn_nmf", fit_on="controls", k=4)
        self.assertEqual(about["fitted_cells"], 3 * 12 * 20)

    def test_a_changed_setting_is_used_and_recorded(self):
        from pertura.steps import programs
        s, _ = program_screen(self.d)
        _, _, about, calls = programs.discover(s, "sklearn_nmf", os.path.join(self.d, "kl"), k=4,
                                               beta_loss="kullback-leibler")
        self.assertEqual(about["settings_changed"], {"beta_loss": "kullback-leibler"})
        self.assertEqual((calls[0]["params"]["beta_loss"], calls[0]["params"]["solver"]), ("kullback-leibler", "mu"))


@unittest.skipUnless(HAS_SCANPY and HAS_CNMF, "needs scanpy and cNMF's environment")
class TestCNMF(Base):
    def test_found_in_the_control_cells_and_every_cell_read_by_cnmfs_refit(self):
        _, _, _, about = self.found("cnmf", fit_on="controls", k=4)
        self.assertEqual(about["k"], 4)


@unittest.skipUnless(HAS_SCANPY and HAS_R, "needs scanpy and R with GeneNMF")
class TestGeneNMF(Base):
    """The synthetic genes are all highly expressed (mean log-normalised expression near 4), above GeneNMF's default
    ceiling for a variable gene (max.exp 3, set for real data): the setting is raised for them."""

    def test_runnmf_finds_the_planted_program(self):
        self.found("genenmf", k=4, max_exp=10.0)

    def test_runnmf_on_the_control_cells(self):
        self.found("genenmf", fit_on="controls", k=4, max_exp=10.0)

    def test_meta_programs_recur_across_the_samples(self):
        _, _, genes, about = self.found("genenmf_meta", k=3, max_exp=10.0)
        self.assertEqual(about["samples"], 3)
        self.assertTrue(about["metrics"])


class TestTheInterface(unittest.TestCase):
    def test_an_unknown_method_or_fit_is_refused(self):
        from pertura.steps import programs
        with self.assertRaises(ValueError) as e:
            programs.discover(None, "pca", ".")
        self.assertIn("cnmf", str(e.exception))
        with self.assertRaises(ValueError):
            programs.fit_cells(type("S", (), {"label": np.array(["a"])})(), "targets")

    def test_each_method_says_what_it_reads_and_how_k_is_set(self):
        from pertura.steps import programs
        self.assertEqual(set(programs.METHODS), {"cnmf", "sklearn_nmf", "genenmf", "genenmf_meta"})
        for name, m in programs.METHODS.items():
            self.assertTrue(m["needs"] and m["k"], name)
            self.assertTrue(m["params"], name)  # each names the package settings a call may change

    def test_a_setting_the_method_does_not_have_is_refused_with_its_settings_named(self):
        from pertura.steps import programs
        with self.assertRaises(ValueError) as e:
            programs.discover(None, "sklearn_nmf", ".", min_confidence=0.7)
        self.assertIn("n_top_genes", str(e.exception))
        self.assertEqual(programs.METHODS["genenmf_meta"]["params"]["min_confidence"][0], 0.5)  # GeneNMF's


class TestScoresCalibration(unittest.TestCase):
    def test_each_programs_groups_give_its_line(self):
        from pertura import blocks as pt
        res = pt.Result("scores", {"targets": ["T1", "T2"]}, pd.DataFrame(
            {"target": ["T1", "T2", "T1", "T2"], "score": ["P1", "P1", "P2", "P2"], "effect": [0.5, 0.01, 0.02, -0.4]}),
            {}, [], "every cell")
        null = pt.Result("scores", {"targets": ["ncg_1", "ncg_2"]}, pd.DataFrame(
            {"target": ["ncg_1", "ncg_2", "ncg_1", "ncg_2"], "score": ["P1", "P1", "P2", "P2"],
             "effect": [0.05, -0.03, 0.1, -0.2], "q": [0.6, 0.9, 0.2, 0.01]}), {}, [], "every cell")
        s = pt.scores_calibration_summary(res, null)
        self.assertEqual(s["by_score"]["P1"]["targets_beyond"], ["T1"])
        self.assertEqual(s["by_score"]["P2"]["targets_beyond"], ["T2"])  # the size, either way
        self.assertEqual((s["pairs_beyond"], s["pairs"], s["groups"]), (2, 4, 2))
        self.assertEqual((s["group_pairs_changed"], s["group_pairs"]), (1, 4))  # what the summary says


@unittest.skipUnless(HAS_SCANPY and HAS_SKLEARN, "needs scanpy and scikit-learn")
class TestCProgramsIsItsRecipe(Base):
    def test_the_module_gives_what_its_blocks_give_and_tells_the_calibration(self):
        from pertura import blocks as pt
        from pertura.question_modules import REGISTRY
        s, design = program_screen(self.d)
        lists = {"planted": PROGRAM, "other": [f"g{i}" for i in range(30, 50)]}
        r = REGISTRY["C.programs"].run(s, design, os.path.join(self.d, "module"), gene_sets="given",
                                       gene_lists=lists)
        with pt.inside(s, os.path.join(self.d, "blocks")):
            res = pt.scores(sets=lists, min_genes=3)
        cols = ["target", "score", "effect", "p", "q"]
        pd.testing.assert_frame_equal(r.table[cols].reset_index(drop=True), res.table[cols].reset_index(drop=True))
        self.assertIn("groups of control guides", r.summary)
        self.assertIn("group x program pairs changed at adjusted p < 0.05", r.summary)
        self.assertIn("by_score", r.diagnostics["calibration"])
        self.assertIn("pt.scores(sets=", r.diagnostics["recipe_as_run"])
        t1 = r.table[(r.table["target"] == "T1") & (r.table["score"] == "planted")]
        self.assertLess(float(t1["q"].iat[0]), 0.05)

    def test_programs_found_in_the_cells_through_the_recipe(self):
        from pertura.question_modules import REGISTRY
        s, design = program_screen(self.d)
        r = REGISTRY["C.programs"].run(s, design, os.path.join(self.d, "module"), gene_sets="sklearn_nmf", k=4)
        self.assertEqual(r.diagnostics["method"], "sklearn_nmf")
        self.assertIn("pt.programs(method='sklearn_nmf'", r.diagnostics["recipe_as_run"])
        self.assertTrue(any(c.get("package") == "scikit-learn" for c in r.diagnostics["package_calls"]))


if __name__ == "__main__":
    unittest.main()
