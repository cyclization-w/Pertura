"""The genes step (docs/adr/0031, D1a): one plumbing for C.genes, the notebook's pt.genes and a finding's checks.
- Where the design matches references to the mode, a target is tested against its own arm's controls, also when
  the call holds one arm's targets only (a single target, as a finding's checks run it).
- pt.genes, which skipped the arm split before, now gives the module's answer, and states each target's own
  reference cells.
- An unknown backend is refused, with the backends named."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np

from test_open_cell_model import mixed_screen

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
ARM = {f"arm{i}" for i in range(10)}


def called(table, target):
    return set(table[(table["target"] == target) & (table["q"] < 0.05)]["gene"])


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestOneArmAtATime(unittest.TestCase):
    def test_a_single_target_is_tested_against_its_own_arm(self):
        """Before the step, a call of one arm's targets kept the whole screen's reference: both arms' controls."""
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d:
            s, _ = mixed_screen(d)
            t, calls = genes.compare(s, {"G1-a": "wilcoxon"}, "control_guides", os.path.join(d, "one"))
        self.assertFalse(ARM & called(t, "G1-a"), called(t, "G1-a"))  # the arm's own baseline is not G1-a's
        self.assertIn("G1", called(t, "G1-a"))
        self.assertTrue(any("the arm CRISPRa" in c.get("contrast", "") for c in calls), calls)

    def test_the_notebook_block_gives_the_modules_answer(self):
        from pertura import blocks
        from pertura.question_modules import c
        with tempfile.TemporaryDirectory() as d:
            s, _ = mixed_screen(d)
            cwd = os.getcwd()
            os.chdir(d)
            try:
                table, _, _ = blocks._genes(s, ["G1-a", "G1-i"], "control_guides", method="wilcoxon")
            finally:
                os.chdir(cwd)
            module, _ = c.genes_by_leads(s, {x: {"method": "wilcoxon"} for x in ("G1-a", "G1-i")}, "control_guides",
                                         os.path.join(d, "module"))
        for x in ("G1-a", "G1-i"):
            self.assertEqual(called(table, x), called(module, x))
            self.assertFalse(ARM & called(table, x))
        refs = table.drop_duplicates("target").set_index("target")["reference_cells"]
        self.assertEqual(int(refs["G1-a"]), 6 * 40)  # its arm's six control guides, 40 cells each
        self.assertEqual(int(refs["G1-i"]), 6 * 40)

    def test_without_matching_every_control_is_the_reference(self):
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d:
            s, _ = mixed_screen(d, matches=False)
            t, _ = genes.compare(s, {"G1-a": "wilcoxon"}, "control_guides", os.path.join(d, "pooled"))
        self.assertTrue(ARM <= called(t, "G1-a"))  # pooled controls: the arm's genes look like G1-a's effect


def capture_screen(d, batch=3.0):
    """Two captures of one arm; capture S2's cells carry genes b0-b9 at batch times S1's, a difference of the
    captures. Control guide CTRL_1's cells sit in S1 only, the other control guides' in both, as CRISPRai's
    NTC-10-a sits in S1Sa only. Target T (both captures) raises gene t0 fourfold."""
    from test_common_response_and_field_measures import build_screen
    groups = {"T": (120, {"t0": 4.0}), "BATCH": (1, {f"b{i}": 1.0 for i in range(10)})}
    screen, design = build_screen(d, groups, units=("S1", "S2"), controls=6, control_cells=60)
    keep = (screen.label != "BATCH") & ~((screen.unit == "S2") & (screen.top_guide == "CTRL_1"))
    a = screen.adata[keep].copy()
    X = a.X.tolil()
    cols = [list(a.var_names).index(f"b{i}") for i in range(10)]
    for i in np.flatnonzero(a.obs["unit"].to_numpy() == "S2"):
        for j in cols:
            X[i, j] = round(X[i, j] * batch) + 2
    a.X = X.tocsr()
    from pertura.question_modules.data import Screen
    return Screen(d, design, adata=a)


HAS_R = os.path.exists(__import__("pertura.envs", fromlist=["rscript"]).rscript())
BATCH = {f"b{i}" for i in range(10)}


class TestTheCapturesInTheTest(unittest.TestCase):
    """CRISPRai: a test on cells that pools captures read the captures' differences as a control guide's genes (up to
    4,024 pooled, 0 to 9 within any one capture). Seurat's LR with the capture as a latent variable, within the
    captures both sides hold, does not."""

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_pooled_test_on_cells_reads_the_captures_as_genes(self):
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d:
            s = capture_screen(d)
            lab = np.where((s.label == "control") & (s.top_guide == "CTRL_1"), "alone", s.label)
            t, _ = genes.compare(s.with_labels(lab), {"alone": "wilcoxon"}, "control_guides", os.path.join(d, "w"))
        self.assertTrue(BATCH & called(t, "alone"), called(t, "alone"))

    @unittest.skipUnless(HAS_R, "needs R with Seurat")
    def test_lr_with_the_capture_does_not_and_still_finds_a_target(self):
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d:
            s = capture_screen(d)
            lab = np.where((s.label == "control") & (s.top_guide == "CTRL_1"), "alone", s.label)
            t, calls = genes.compare(s.with_labels(lab), {"alone": "seurat_lr", "T": "seurat_lr"}, "control_guides",
                                     os.path.join(d, "lr"))
        self.assertFalse(BATCH & called(t, "alone"), called(t, "alone"))
        self.assertIn("t0", called(t, "T"))
        self.assertTrue({"q_package"} <= set(t.columns))
        self.assertTrue(all(c["covariates"] == ["sample"] for c in calls), calls)
        self.assertIn("within the samples both hold", calls[0]["contrast"])

    @unittest.skipUnless(HAS_R, "needs R with Seurat")
    def test_a_seurat_setting_changed_from_its_default_reaches_findmarkers_and_is_recorded(self):
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d:
            s = capture_screen(d)
            default, _ = genes.compare(s, {"T": "seurat_lr"}, "control_guides", os.path.join(d, "a"))
            strict, calls = genes.compare(s, {"T": "seurat_lr"}, "control_guides", os.path.join(d, "b"),
                                          params={"logfc_threshold": 1.0})
        self.assertLess(len(strict), len(default))  # fewer genes pass Seurat's own filter
        self.assertGreaterEqual(strict["effect"].abs().min(), 1.0 - 1e-9)
        self.assertEqual(calls[0]["params"]["logfc_threshold"], 1.0)
        self.assertIn("changed from Seurat's", calls[0]["params"]["defaults"])


class TestCalibrationGroupsWithinTheArm(unittest.TestCase):
    def test_each_group_is_drawn_within_one_arm_and_each_arm_keeps_a_reference(self):
        """Drawn from every control guide, 3 of CRISPRai's 5 groups mixed the activation and the interference arm."""
        from pertura.question_modules import c
        with tempfile.TemporaryDirectory() as d:
            s, _ = mixed_screen(d)
            groups = c.calibration_groups(s)
            mode = s.attribute("mode")
            self.assertTrue(groups)
            for g, guides in groups.items():
                self.assertEqual(len({mode[x] for x in guides}), 1, (g, guides))
            for arm in ("CRISPRa", "CRISPRi"):
                arm_guides = {x for x in set(s.top_guide[s.label == "control"]) if mode.get(x) == arm}
                used = {x for gl in groups.values() for x in gl if mode[x] == arm}
                self.assertTrue(used and arm_guides - used, arm)  # some groups, and guides left as the reference
            s2, _ = mixed_screen(d, matches=False)
            self.assertTrue(c.calibration_groups(s2))  # without matching: drawn from every control guide, as before


HAS_PYDESEQ2 = importlib.util.find_spec("pydeseq2") is not None


@unittest.skipUnless(HAS_PYDESEQ2, "needs pydeseq2")
class TestPyDESeq2(unittest.TestCase):
    def test_pseudobulk_by_sample_paired_finds_the_planted_genes(self):
        """Four mice: each target's pseudobulk against the control cells' in the mice both hold (~ unit + condition)."""
        from pertura.steps import genes
        from test_common_response_and_field_measures import build_screen
        with tempfile.TemporaryDirectory() as d:
            s, _ = build_screen(d, {"T": (60, {f"t{i}": 3.0 for i in range(10)}), "N": (60, {})},
                                units=("m1", "m2", "m3", "m4"), genes=120, controls=6, control_cells=30)
            t, calls = genes.compare(s, {"T": "pydeseq2", "N": "pydeseq2"}, "control_guides", os.path.join(d, "p"))
        found = set(t[(t["target"] == "T") & (t["q"] < 0.05)]["gene"])
        self.assertTrue({f"t{i}" for i in range(10)} <= found, found)
        self.assertLess(len(set(t[(t["target"] == "N") & (t["q"] < 0.05)]["gene"])), 3)
        self.assertEqual(calls[0]["formula"], "~ unit + condition")

    def test_too_few_samples_is_not_fitted(self):
        from pertura.steps import genes
        from test_common_response_and_field_measures import build_screen
        with tempfile.TemporaryDirectory() as d:
            s, _ = build_screen(d, {"T": (60, {"t0": 3.0})}, units=("m1", "m2"), genes=60, controls=6)
            t, calls = genes.compare(s, {"T": "pydeseq2"}, "control_guides", os.path.join(d, "p"))
        self.assertEqual(genes.fitted("pydeseq2", t, ["T"], calls), [])


HAS_R = os.path.exists(__import__("pertura.envs", fromlist=["rscript"]).rscript())


class TestTheOtherPseudobulksAndTheTTest(unittest.TestCase):
    """limma-voom, R's DESeq2 and glmGamPoi on the edgeR backend's pseudobulk, and scanpy's t-test on cells: each
    finds the planted genes of T and few of the inert N's (four mice)."""

    def run_backend(self, name):
        from pertura.steps import genes
        from test_common_response_and_field_measures import build_screen
        with tempfile.TemporaryDirectory() as d:
            s, _ = build_screen(d, {"T": (60, {f"t{i}": 3.0 for i in range(10)}), "N": (60, {})},
                                units=("m1", "m2", "m3", "m4"), genes=120, controls=6, control_cells=30)
            t, calls = genes.compare(s, {"T": name, "N": name}, "control_guides", os.path.join(d, "p"))
        found = called(t, "T")
        self.assertTrue({f"t{i}" for i in range(10)} <= found, (name, found))
        self.assertLess(len(called(t, "N")), 3, name)
        self.assertEqual(set(t["test"]), {name})
        return calls

    @unittest.skipUnless(HAS_R, "needs R (limma, DESeq2, glmGamPoi)")
    def test_the_r_pseudobulks(self):
        for name, package in (("limma_voom", "limma"), ("deseq2", "DESeq2"), ("glmgampoi", "glmGamPoi")):
            calls = self.run_backend(name)
            self.assertEqual(calls[0]["package"], package)
            self.assertEqual(calls[0]["formula"], "~ unit + group")

    @unittest.skipUnless(HAS_R, "needs R (sceptre)")
    def test_sceptre_against_the_non_targeting_cells(self):
        calls = self.run_backend("sceptre")
        self.assertEqual(calls[0]["package"], "sceptre")
        self.assertIn("+ sample", calls[0]["formula"])  # four mice

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_t_test_on_cells(self):
        calls = self.run_backend("ttest")
        self.assertEqual(calls[0]["params"]["method"], "t-test")


class TestTheBackends(unittest.TestCase):
    def test_an_unknown_backend_is_refused_with_the_backends_named(self):
        from pertura.steps import genes
        with self.assertRaises(ValueError) as e:
            genes.compare(None, {"A": "deseq9"}, "control_guides", ".")
        self.assertIn("wilcoxon", str(e.exception))

    def test_a_setting_no_backend_of_the_call_has_is_refused_with_their_settings_named(self):
        """A setting that would change nothing is refused, not ignored."""
        from pertura.steps import genes
        with self.assertRaises(ValueError) as e:
            genes.compare(None, {"A": "wilcoxon"}, "control_guides", ".", params={"logfc_threshold": 0.25})
        self.assertIn("logfc_threshold", str(e.exception))
        genes.check_params({"seurat_lr", "wilcoxon"}, {"logfc_threshold": 0.25})  # LR has it: taken
        self.assertEqual(genes.BACKENDS["mast"]["params"]["min_pct"][0], 0.01)  # Seurat's default

    def test_each_backend_says_what_it_reads_and_what_its_q_means(self):
        from pertura.steps import genes
        for name, b in genes.BACKENDS.items():
            self.assertTrue(b["needs"] and b["q"], name)
        self.assertEqual(set(genes.ORDER), set(genes.BACKENDS))


class TestTheCellTestsInParts(unittest.TestCase):
    def test_the_parts_are_filled_by_their_cells(self):
        from pertura.question_modules.c import parts_by_load
        cells = [1614, 1610, 1404, 1281, 980, 700, 650, 400, 300, 120, 90, 60]
        part = parts_by_load(cells, 4)
        self.assertEqual(sorted(set(part)), [0, 1, 2, 3])
        loads = [sum(c for c, p in zip(cells, part) if p == k) for k in range(4)]
        by_turn = [sum(cells[k::4]) for k in range(4)]  # the split by turn, before
        self.assertLess(max(loads) - min(loads), max(by_turn) - min(by_turn))
        self.assertEqual(list(parts_by_load(cells, 4)), list(part))  # the same split every time
        self.assertEqual(list(parts_by_load([5, 5, 5], 3)), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
