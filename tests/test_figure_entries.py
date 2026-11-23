"""Figures as record entries (docs/adr/0021, section 2; build step 4, bar 1): save_figure writes the PNG at 300 dpi, a
PDF with editable text and the plotted data, and the tool enters figure_saved; a figure that fails the layout checks
is not written; module runs enter their figures when they end."""
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

from pertura import record as rc, runner
from pertura.question_modules import figures


def chart(width_mm=89, label="Share of cells"):
    plt = figures._plt()
    fig, ax = plt.subplots(figsize=(width_mm * figures.MM, 50 * figures.MM))
    ax.bar(["control", "A", "B"], [0.2, 0.5, 0.1], color=figures.CONTROL_GREY)
    ax.set_ylabel(label)
    fig.tight_layout(pad=0.4)
    return plt, fig


DATA = pd.DataFrame({"group": ["control", "A", "B"], "share": [0.2, 0.5, 0.1]})


class TestSaveFigure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "figures")

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_writes_the_png_the_pdf_the_data_and_the_sidecar(self):
        from PIL import Image
        plt, fig = chart()
        s = figures.save_figure(fig, "shares", DATA, "Share of cells per group", "overview", folder=self.folder)
        plt.close(fig)
        self.assertEqual(s["checks"], [])
        with Image.open(s["png"]) as im:
            self.assertAlmostEqual(im.info["dpi"][0], 300, delta=1)
            self.assertEqual(im.info.get("Title"), "Share of cells per group")
        pdf = open(s["pdf"], "rb").read()
        self.assertIn(b"/FontFile2", pdf)  # TrueType embedded: the text stays editable
        back = pd.read_csv(s["data"][0], sep="\t")
        pd.testing.assert_frame_equal(back, DATA)
        side = json.load(open(os.path.join(self.folder, "shares" + figures.SIDECAR), encoding="utf-8"))
        self.assertEqual(side["role"], "overview")
        self.assertEqual(side["caption"], "Share of cells per group")

    def test_several_tables_and_a_kept_index(self):
        plt, fig = chart()
        matrix = pd.DataFrame([[1.0, -1.0]], index=["A"], columns=["g1", "g2"])
        s = figures.save_figure(fig, "two", {"bars": DATA, "matrix": matrix}, "Two tables", folder=self.folder)
        plt.close(fig)
        self.assertEqual([os.path.basename(p) for p in s["data"]], ["two.data.bars.tsv", "two.data.matrix.tsv"])
        self.assertEqual(pd.read_csv(s["data"][1], sep="\t", index_col=0).index.tolist(), ["A"])

    def test_a_figure_that_fails_its_checks_is_saved_with_its_notes(self):
        # docs/adr/0032, D4: a figure kept while exploring is saved as it is; the checks note, a panel holds them
        plt, fig = chart()
        fig.axes[0].text(0.5, 0.5, "far too small", fontsize=2)
        s = figures.save_figure(fig, "tiny", DATA, "Tiny text", folder=self.folder)
        plt.close(fig)
        self.assertTrue(any("below" in i for i in s["checks"]), s["checks"])
        self.assertTrue(os.path.exists(s["png"]))
        self.assertFalse(s["strict"])
        self.assertIn("strict=True", s["for_a_panel"])

    def test_with_strict_a_figure_that_fails_its_checks_is_not_written(self):
        plt, fig = chart()
        fig.axes[0].text(0.5, 0.5, "far too small", fontsize=2)
        with self.assertRaises(ValueError) as e:
            figures.save_figure(fig, "tiny", DATA, "Tiny text", folder=self.folder, strict=True)
        plt.close(fig)
        self.assertIn("below", str(e.exception))
        self.assertFalse(os.path.exists(self.folder) and os.listdir(self.folder))

    def test_a_width_between_columns_is_kept_and_noted_and_strict_sets_the_nearer_column(self):
        plt, fig = chart(width_mm=120)  # attempt 3's figures were refused for their width 4 times of 13
        s = figures.save_figure(fig, "wide", DATA, "Was 120 mm wide", folder=self.folder)
        self.assertNotIn("resized", s)
        self.assertTrue(any("width 120 mm" in i for i in s["checks"]), s["checks"])
        s = figures.save_figure(fig, "wide", DATA, "Was 120 mm wide", folder=self.folder, strict=True)
        plt.close(fig)
        self.assertEqual(s["resized"], "width set from 120 mm to 89 mm")
        self.assertTrue(os.path.exists(s["png"]))

    def test_caption_and_data_are_noted_when_missing_and_required_with_strict(self):
        plt, fig = chart()
        s = figures.save_figure(fig, "bare", folder=self.folder)
        self.assertEqual((s["caption"], s["data"], s["notes"]), (None, [], ["no caption", "no plotted data"]))
        for kwargs in ({"caption": ""}, {"data": None}, {"data": pd.DataFrame()}):
            args = {"data": DATA, "caption": "A caption", **kwargs}
            with self.assertRaises(ValueError, msg=kwargs):
                figures.save_figure(fig, "ok", args["data"], args["caption"], folder=self.folder, strict=True)
        plt.close(fig)

    def test_name_and_empty_data_are_refused_as_stated(self):
        plt, fig = chart()
        for kwargs in ({"data": pd.DataFrame()}, {"name": "../up"}, {"name": "a b"}):
            args = {"name": "ok", "data": DATA, "caption": "A caption", "role": None, **kwargs}
            with self.assertRaises(ValueError, msg=kwargs):
                figures.save_figure(fig, args["name"], args["data"], args["caption"], args["role"],
                                    folder=self.folder)
        plt.close(fig)

    def test_a_role_in_other_words_is_kept_as_a_note(self):
        # the fair comparison's D: role='check' or 'doubt test' was refused four times, each a cell lost
        plt, fig = chart()
        s = figures.save_figure(fig, "ok", DATA, "A caption", "doubt test", folder=self.folder)
        plt.close(fig)
        self.assertIsNone(s["role"])
        self.assertTrue(any("'doubt test' kept as a note" in n for n in s["notes"]), s["notes"])
        self.assertNotIn("for_a_panel", s)

    def test_a_figure_closed_by_pyplot_is_checked_and_saved(self):
        # the notebook closes each cell's figures; saving one in a later cell crashed the checks (B 3 times, D once)
        from matplotlib.backend_bases import FigureCanvasBase
        plt, fig = chart()
        plt.close(fig)
        FigureCanvasBase(fig)  # what matplotlib 3.11 leaves on a closed figure, whatever the version here
        s = figures.save_figure(fig, "later", DATA, "Drawn in an earlier cell", folder=self.folder, strict=True)
        self.assertEqual(s["checks"], [])
        self.assertTrue(os.path.exists(s["png"]))

    def test_text_cut_at_the_edge_is_an_issue(self):
        plt, fig = chart()
        fig.text(0.98, 0.5, "a long label that runs off the edge")
        issues = figures.check_figure(fig)
        plt.close(fig)
        self.assertTrue(any("cut at the figure's edge" in i for i in issues), issues)


class TestFiguresInTheRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = rc.Record(os.path.join(self.tmp.name, "record"), {"research_lead": True, "figure_review": True})  # the ablation (ADR 0052)
        self.rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "x", "inputs": []}, rc.TOOL)

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, **kw):
        return {"figure_id": "shares", "run_id": "r1", "paths": {"png": "shares.png"}, "caption": "Shares", **kw}

    def test_figure_saved_is_checked(self):
        for bad, why in ((self.entry(run_id="r9"), "no run r9"), (self.entry(paths={}), "png"),
                         (self.entry(caption=" "), "caption"), (self.entry(role="main"), "role is one of")):
            with self.assertRaises(rc.RecordError) as e:
                self.rec.append("figure_saved", bad, rc.TOOL)
            self.assertIn(why, str(e.exception))
        with self.assertRaises(rc.RecordError):
            self.rec.append("figure_saved", self.entry(), rc.AGENT)  # the tools enter figures, not the agent
        self.rec.append("figure_saved", self.entry(caption=None, strict=False, notes=["no caption"]), rc.TOOL)
        f = self.rec.state()["figures"]["shares"]
        self.assertEqual((f["caption"], f["strict"], f["notes"]), (None, False, ["no caption"]))

    def test_saving_again_makes_a_new_version_and_replay_gives_the_same_state(self):
        self.rec.append("figure_saved", self.entry(role="overview"), rc.TOOL)
        self.rec.append("figure_saved", self.entry(paths={"png": "shares_v2.png"}, caption="Shares, fixed"), rc.TOOL)
        f = self.rec.state()["figures"]["shares"]
        self.assertEqual((f["version"], f["paths"]["png"], f["caption"]), (2, "shares_v2.png", "Shares, fixed"))
        self.assertEqual(f["earlier"][0]["paths"]["png"], "shares.png")
        self.assertEqual(rc.reduce(self.rec.events()), self.rec.state())

    def test_a_jobs_figures_are_entered_when_it_ends(self):
        root = os.path.join(self.tmp.name, "jobs")
        code = ("import json, matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
                "for name in ('figure', 'figure_composition', 'figure_part_a'):\n"
                "    fig, ax = plt.subplots()\n    ax.plot([0, 1])\n"
                "    fig.savefig(name + '.png', metadata={'Title': 'Title of ' + name})\n"
                "open('result.tsv', 'w').write('a\\n1\\n')\nopen('by_unit.tsv', 'w').write('a\\n1\\n')\n"
                "json.dump({'summary': 's', 'tables': ['result', 'by_unit'], 'figure_checks': "
                "{'units_as_dots': True}}, open('result.json', 'w'))\n")
        rec = rc.Record(os.path.join(self.tmp.name, "record_of_jobs"))
        rid = rec.start_run("a module", [sys.executable, "-c", code], [], root, name="module")
        runner.wait(rid, root, timeout=120)
        self.assertEqual(rec.sync_runs(root), [rid])
        st = rec.state()
        figs = {k: v for k, v in st["figures"].items() if v["run_id"] == rid}
        self.assertEqual(sorted(figs), [f"{rid}:figure", f"{rid}:figure_composition"])  # parts stay in the whole
        main = figs[f"{rid}:figure"]
        self.assertEqual(main["caption"], "Title of figure")
        self.assertEqual(main["figure_checks"], {"units_as_dots": True})
        self.assertEqual([os.path.basename(p) for p in main["paths"]["data"]], ["result.tsv", "by_unit.tsv"])
        self.assertEqual(st["runs"][rid]["figure"], main["paths"]["png"])


class TestFiguresThroughTheService(unittest.TestCase):
    """save_figure in a notebook cell, and a module run, through the analysis server on the synthetic deposit."""

    @classmethod
    def setUpClass(cls):
        from test_design_service import answer, confirm_the_cells, make_deposit, proposal

        from pertura import design_gate, screen_object
        from pertura.analysis_service import AnalysisService
        cls.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        cls.svc = AnalysisService({"workspace": os.path.join(cls.tmp.name, "ws")})
        for tool, args, elicit in (("open_project", {"data_folder": deposit}, None), ("measure", {}, None),
                                   ("check_design", {"proposal": proposal()}, None),
                                   ("confirm_design", {}, answer())):
            ok, out = cls.svc.call(tool, args, elicit=elicit)
            assert ok, out
        design = design_gate.load(cls.svc.design.design_file)[0]
        screen_object.build(deposit, design, cls.svc._screen_dir())
        confirm_the_cells(cls.svc)

    @classmethod
    def tearDownClass(cls):
        cls.svc.close()
        cls.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def test_pt_save_figure_is_the_notebooks_own(self):
        """Session 10's agent called pt.save_figure twice: an AttributeError, as save_figure was the notebook's alone."""
        code = ("from pertura.question_modules import figures as F\nplt = F._plt()\n"
                "fig, ax = plt.subplots(figsize=(F.SINGLE, F.HEIGHT))\nax.bar([0, 1], [3, 4], color=F.CONTROL_GREY)\n"
                "ax.set_ylabel('Cells')\nfig.tight_layout(pad=0.4)\npt.save_figure(fig, 'two_bars')\nplt.close(fig)\n")
        run = self.ok("run_code", what="two bars by pt.save_figure", code=code)
        self.assertEqual(run["status"], "ok", run)
        self.assertEqual(run["figures_entered"], ["two_bars"])

    def test_a_cell_that_saves_a_figure_enters_it(self):
        code = ("import pandas as pd\nfrom pertura.question_modules import figures as F\nplt = F._plt()\n"
                "c = pd.read_csv(screen + '/cells.tsv.gz', sep='\\t')\n"
                "n = c['label'].value_counts().rename_axis('label').reset_index(name='cells')\n"
                "fig, ax = plt.subplots(figsize=(F.SINGLE, F.HEIGHT))\n"
                "ax.bar(range(len(n)), n['cells'], color=F.CONTROL_GREY)\nax.set_ylabel('Cells')\n"
                "fig.tight_layout(pad=0.4)\n"
                "save_figure(fig, 'cells_per_label', n, 'Cells per label', 'evidence')\nplt.close(fig)\n")
        run = self.ok("run_code", what="cells per label", code=code)
        self.assertEqual(run["status"], "ok", run)
        self.assertEqual(run["figures_entered"], ["cells_per_label"])
        f = self.ok("read_record", what="figures", ids=["cells_per_label"])["figures"]["cells_per_label"]
        self.assertEqual((f["run_id"], f["role"], f["caption"]), (run["run_id"], "evidence", "Cells per label"))
        for p in (f["paths"]["png"], f["paths"]["pdf"], f["paths"]["data"][0]):
            self.assertTrue(os.path.exists(p), p)
        again = self.ok("run_code", what="the same, unchanged", code="print('nothing drawn')")
        self.assertEqual(again["figures_entered"], [])  # an unchanged figure is not entered again
        self.assertEqual(len(run["figures_shown"]), 1)  # the saved figure, shown to the agent once
        self.assertTrue(run["figures_shown"][0].endswith("cells_per_label.png"))
        small = code.replace("ax.set_ylabel('Cells')", "ax.set_ylabel('Cells', fontsize=2)")
        kept = self.ok("run_code", what="a figure with text too small", code=small)
        self.assertEqual(kept["status"], "ok", kept)  # kept while exploring, its issues noted
        self.assertEqual(kept["figures_entered"], ["cells_per_label"])
        f = self.ok("read_record", what="figures", ids=["cells_per_label"])["figures"]["cells_per_label"]
        self.assertTrue(any("below" in i for i in f["checks"]), f["checks"])
        self.assertIs(f["strict"], False)
        bad = self.ok("run_code", what="the same figure, for a panel",
                      code=small.replace("'evidence')", "'evidence', strict=True)"))
        self.assertEqual(bad["status"], "error")
        self.assertIn("fails its checks", bad["error"])
        self.assertEqual(bad["figures_entered"], [])

    def test_a_module_run_enters_its_figure(self):
        out = self.ok("run_module", module="B.abundance", wait=240)
        self.assertEqual(out["state"], "done", out)
        figs = self.ok("read_record", what="figures")["figures"]
        self.assertIn(f"{out['run_id']}:figure", figs)  # each figure in one line: its caption
        self.assertTrue(figs[f"{out['run_id']}:figure"])


if __name__ == "__main__":
    unittest.main()
