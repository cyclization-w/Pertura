"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 4), fixed
for any screen (docs/build_goal_session6_fixes.md, item 7): a finding about one gene reran Seurat's LR on every gene
for each guide, a second test, four control-guide groups and depth-matched controls, 26 minutes, and nobody was told
when it ended; C.genes on every target took about 40 minutes. Now:
- a finding's re-tests test its gene alone where the test allows it (on cells, or Seurat's on its features);
- the calibration is computed once per build, test and reference, and reused;
- C.genes takes the targets to test, and before a long run the reply gives the time of one target alone;
- a long step writes its progress, which the run's state, the replies and the status line show."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

from test_common_response_and_field_measures import build_screen

from pertura import progress
from pertura.analysis_service import AnalysisService
from pertura.question_modules import c
from pertura.steps import genes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import status_file as sf  # noqa: E402
import status_words as words  # noqa: E402

NOW = 1_800_000_000.0


def screen_with_groups(d):
    """A target changing G_UP, and the control cells split into calibration groups, as c.calibrate_method labels
    them."""
    s, _ = build_screen(d, {"A": (60, {"G_UP": 3.0, "G_DOWN": 0.3})}, units=("u1", "u2"), controls=12,
                        control_cells=15)
    groups = c.calibration_groups(s)
    lab = s.label.copy()
    for g, gl in groups.items():
        lab = np.where((s.label == "control") & np.isin(s.top_guide, gl), g, lab)
    return s, s.with_labels(lab), sorted(groups)


class TestTheFindingsGeneAlone(unittest.TestCase):
    def test_only_its_rows_with_q_over_it_alone(self):
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = screen_with_groups(d)
            every, _ = genes.compare(s, {"A": "seurat3_lr"}, "control_guides", d)
            alone, calls = genes.compare(s, {"A": "seurat3_lr"}, "control_guides", d, genes=["G_UP"])
        self.assertEqual(list(alone["gene"]), ["G_UP"])
        row = every[every["gene"] == "G_UP"].iloc[0]
        self.assertAlmostEqual(float(alone["p"].iat[0]), float(row["p"]))  # the same test of the same gene
        self.assertAlmostEqual(float(alone["q"].iat[0]), float(alone["p"].iat[0]))  # one gene: its q is its p
        self.assertIn("only G_UP", calls[0]["genes"])

    def test_the_cell_tests_and_seurats_test_the_genes_alone_and_the_others_fit_on_every_gene(self):
        self.assertEqual(set(genes.CELL_LEVEL) | set(genes.FEATURES),
                         {"wilcoxon_same_samples", "wilcoxon", "ttest", "seurat3_lr", "mast", "seurat_lr"})
        self.assertFalse({"edger_ql", "pydeseq2", "sceptre"} & (set(genes.CELL_LEVEL) | set(genes.FEATURES)))

    def test_a_gene_cut_screen_keeps_each_cells_whole_library_size(self):
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = screen_with_groups(d)
            cut = s.with_genes(["G_UP"])
            j = s.gene_index["G_UP"]
            self.assertEqual(list(cut.genes), ["G_UP"])
            self.assertTrue(np.allclose(cut.lognorm.toarray()[:, 0], s.lognorm[:, j].toarray()[:, 0]))


class TestTheCalibrationOncePerBuild(unittest.TestCase):
    def test_a_second_call_on_the_same_cells_reads_it_and_other_cells_compute_it_again(self):
        with tempfile.TemporaryDirectory() as d:
            _, null, groups = screen_with_groups(d)
            leads = {g: "seurat3_lr" for g in groups}
            first, calls = genes.compare(null, leads, "control_guides", d)
            self.assertNotIn("calibration", calls[0])
            again, calls = genes.compare(null, leads, "control_guides", d)
            self.assertEqual(len(first), len(again))
            self.assertIn("computed once", calls[0]["calibration"])
            moved = null.label.copy()
            moved[np.flatnonzero(null.label == groups[0])[0]] = "control"  # one cell back in the reference
            _, calls = genes.compare(null.with_labels(moved), leads, "control_guides", d)
            self.assertNotIn("calibration", calls[0])

    def test_a_targets_call_is_not_kept(self):
        with tempfile.TemporaryDirectory() as d:
            s, _, _ = screen_with_groups(d)
            genes.compare(s, {"A": "seurat3_lr"}, "control_guides", d)
            self.assertFalse(os.path.isdir(os.path.join(s.dir, genes.CACHE)))


class TestTheTimeOfOneTargetAlone(unittest.TestCase):
    def test_said_beside_the_whole_run(self):
        said = AnalysisService._one_target(38, 180)
        self.assertIn("One target alone (inputs targets: [its label]) takes about 3 minutes", said)
        self.assertIn("first time", said)
        self.assertEqual(AnalysisService._one_target(1, 180), "")

    def test_c_genes_takes_the_targets(self):
        from pertura import question_modules as qm
        self.assertIn("targets", qm.REGISTRY["C.genes"].contract.inputs)


class TestProgress(unittest.TestCase):
    def test_a_step_writes_it_in_the_jobs_folder_and_it_reads_in_words(self):
        old = os.environ.get(progress.VARIABLE)
        saved = dict(progress._state)
        try:
            with tempfile.TemporaryDirectory() as d:
                os.environ[progress.VARIABLE] = d
                progress._state.update(done=0, of=0, started=None)
                progress.expect(12)
                for _ in range(7):
                    progress.advance()
                p = progress.read(d)
                self.assertEqual((p["done"], p["of"]), (7, 12))
                said = progress.words({**p, "started": NOW - 7 * 60}, now=NOW)
                self.assertEqual(said, "7 of 12 comparisons, about 5 minutes left")
        finally:
            progress._state.clear()
            progress._state.update(saved)
            if old is None:
                os.environ.pop(progress.VARIABLE, None)
            else:
                os.environ[progress.VARIABLE] = old

    def test_outside_a_job_nothing_is_written(self):
        old = os.environ.pop(progress.VARIABLE, None)
        try:
            with tempfile.TemporaryDirectory() as d:
                progress.expect(1)
                self.assertEqual(os.listdir(d), [])
        finally:
            if old is not None:
                os.environ[progress.VARIABLE] = old

    def test_the_status_line_shows_it(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "run_module", {"module": "C.genes"},
                      {"run_id": "r1", "state": "running", "progress": "7 of 12 comparisons, about 9 minutes left"},
                      now=NOW)
        self.assertIn("\tC.genes\t7 of 12 comparisons, about 9 minutes left", sf.line_file(st))
        line = words.plain(words.job_line([(NOW - 600, NOW - 5, "C.genes", "7 of 12 comparisons, about 9 minutes "
                                                                          "left")], [], None, NOW))
        self.assertIn("C.genes 10 min (7 of 12 comparisons, about 9 minutes left)", line)


HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
HAS_R = shutil.which("Rscript") is not None or os.path.exists(
    __import__("pertura.envs", fromlist=["rscript"]).rscript())
WILCOXON = {"method": "wilcoxon", "reason": "a test on cells (Wilcoxon), the test the finding came from"}


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestTheChecksWithTheirPackages(unittest.TestCase):
    def setUp(self):
        from test_design_fit import screen
        self.d = tempfile.mkdtemp()
        self.s, self.design = screen(self.d, independent="none", context="cell_line")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_the_lead_keeps_every_gene_and_the_re_tests_test_the_gene_alone(self):
        from pertura import finding_checks as fc
        sp = {"target": "B", "feature": "b0", "direction": 1, "kind": "gene", "reference": "control_guides",
              "lead": WILCOXON}
        unit = self.s.unit.copy()  # B's one guide over the four lanes: halves of the units, each re-tested
        b = np.flatnonzero(self.s.cells_of("B"))
        unit[b] = np.array(["u1", "u2", "u3", "u4"])[np.arange(len(b)) % 4]
        self.s.unit = unit
        lead, out = fc.run_checks(self.s, sp, os.path.join(self.d, "work"))
        c = {x["check"]: x for x in out}
        self.assertIsNotNone(lead["genes_changed"])  # the calibration reads the lead's changed genes
        self.assertNotIn("gene_alone", lead)
        self.assertTrue(all(v.get("gene_alone") for v in c["half"]["parts"].values()), c["half"])
        self.assertEqual(c["half"]["outcome"], "agree")
        self.assertIn("each re-test tested b0 alone", fc.sentence(sp, lead, out))

    def test_c_genes_and_a_findings_checks_share_one_calibration(self):
        from pertura import finding_checks as fc
        from pertura import question_modules as qm
        qm.REGISTRY["C.genes"].run(self.s, self.design, os.path.join(self.d, "genes"), method="wilcoxon")
        kept = os.listdir(os.path.join(self.s.dir, genes.CACHE))
        sp = {"target": "B", "feature": "b0", "direction": 1, "kind": "gene", "reference": "control_guides",
              "lead": WILCOXON, "only": ["calibration"]}
        _, out = fc.run_checks(self.s, sp, os.path.join(self.d, "work"))
        self.assertEqual(out[0]["outcome"], "beyond the control groups")
        self.assertEqual(sorted(os.listdir(os.path.join(self.s.dir, genes.CACHE))), sorted(kept))  # read, not made

    def test_c_genes_on_some_targets(self):
        from pertura import question_modules as qm
        r = qm.REGISTRY["C.genes"].run(self.s, self.design, os.path.join(self.d, "genes"), method="wilcoxon",
                                       targets=["B"])
        self.assertEqual(list(r.table["target"]), ["B"])
        with self.assertRaises(ValueError):
            qm.REGISTRY["C.genes"].run(self.s, self.design, os.path.join(self.d, "g2"), method="wilcoxon",
                                       targets=["NOT_A_TARGET"])

    @unittest.skipUnless(HAS_R, "needs R (Seurat)")
    def test_seurat_tests_the_features_alone_and_counts_its_comparisons(self):
        old = os.environ.get(progress.VARIABLE)
        saved = dict(progress._state)
        try:
            os.environ[progress.VARIABLE] = self.d
            progress._state.update(done=0, of=0, started=None)
            table, _, _, _, _ = c.genes_field(self.s, self.design, ["B"], "control_guides",
                                              os.path.join(self.d, "lr"), calibrate=False, test="LR",
                                              features=["b0"])
            p = progress.read(self.d)
        finally:
            progress._state.clear()
            progress._state.update(saved)
            if old is None:
                os.environ.pop(progress.VARIABLE, None)
            else:
                os.environ[progress.VARIABLE] = old
        self.assertEqual(list(table["gene"]), ["b0"])
        self.assertEqual((p["done"], p["of"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
