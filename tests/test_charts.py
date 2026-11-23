"""The charts published screens draw (docs/build_goal_adr0022.md, step 4, bar 1): each generic chart function draws
on synthetic data, passes the layout checks (no text below 5 pt, none overlapping or cut, a column width, editable
text), and writes its PNG and PDF."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura.question_modules import charts


class TestCharts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rng = np.random.default_rng(0)
        cls.targets = [f"T{i:02d}" for i in range(30)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drawn(self, name, issues):
        path = os.path.join(self.tmp.name, f"{name}.png")
        self.assertEqual(issues, [], name)
        self.assertTrue(os.path.exists(path) and os.path.exists(path[:-4] + ".pdf"), name)

    def path(self, name):
        return os.path.join(self.tmp.name, f"{name}.png")

    def test_ranked_targets(self):
        t = pd.DataFrame({"target": self.targets, "value": self.rng.normal(0, 1, 30),
                          "q": self.rng.uniform(0, 0.2, 30)})
        self.drawn("ranked", charts.ranked_targets(t, "value", self.path("ranked"), "E-distance vs control cells",
                                                   "ranked", q="q", control_band=(-0.2, 0.2)))

    def test_ranked_targets_each_against_its_own_line(self):
        """own_line: each target's line across its bar; a bar not past its own line is light, one without a line
        carries a ring; the layout checks pass."""
        t = pd.DataFrame({"target": self.targets[:6], "value": [2.0, 1.5, 1.0, 0.6, 0.3, 0.2],
                          "line": [0.5, 1.8, 0.4, np.nan, 0.4, 0.5]})
        self.drawn("own_line", charts.ranked_targets(t, "value", self.path("own_line"), "Energy distance vs control "
                                                     "cells", "own line", own_line="line"))

    def test_what_a_chart_greys_or_marks_is_named_in_its_key(self):
        """The control band, the muted bars, each target's own line and the changed genes' colours are named in a
        key above the plot, not left to the caption; a chart that greys and marks nothing has no key."""
        import matplotlib.pyplot as plt

        def key(draw):
            fig = plt.figure()
            draw(fig)
            legend = fig.axes[0].get_legend()
            plt.close(fig)
            return [t.get_text() for t in legend.get_texts()] if legend else []
        t = pd.DataFrame({"target": self.targets[:6], "value": [2.0, 1.5, 1.0, 0.6, 0.3, 0.2],
                          "line": [0.5, 1.8, 0.4, np.nan, 0.4, 0.5]})
        self.assertEqual(key(lambda f: charts.ranked_targets(t, "value", None, "", "", control_band=(0, 0.4),
                                                             muted=({self.targets[3]}, "Left out"), own_line="line",
                                                             fig=f)),
                         ["Threshold", "No threshold", "Left out; Below threshold", "Control guides (range)"])
        self.assertEqual(key(lambda f: charts.ranked_targets(t, "value", None, "", "", fig=f)), [])
        genes = pd.DataFrame({"gene": [f"g{i}" for i in range(6)], "effect": [-2, -1, 0.1, 1, 2, 3],
                              "q": [0.001, 0.2, 0.5, 0.3, 0.01, 0.001]})
        self.assertEqual(key(lambda f: charts.volcano(genes, None, "", fig=f)),
                         ["Down (adj. p < 0.05)", "Up (adj. p < 0.05)"])

    def test_embedding_values_and_distributions(self):
        xy = self.rng.normal(0, 1, (400, 2))
        self.drawn("by_value", charts.embedding_values(xy, self.rng.uniform(1e2, 1e4, 400), self.path("by_value"),
                                                       "UMIs", log=True))
        t = pd.DataFrame({"sample": np.repeat([f"s{i}" for i in range(8)], 50),
                          "umis": self.rng.lognormal(8, 1, 400)})
        self.drawn("distributions", charts.distributions(t, "umis", "sample", self.path("distributions"),
                                                         "UMIs per cell", log=True, cuts=[500]))
        few = t[t["sample"].isin(["s0", "s1"])]
        self.drawn("two_samples", charts.distributions(few, "umis", "sample", self.path("two_samples"), "UMIs",
                                                       key=False))

    def test_the_old_figure_kinds_run_the_layout_checks(self):
        """figures.dots, bars and the rest return the layout checks' issues of the figure they saved, and their
        widths are one or two columns, never between."""
        from pertura.question_modules import figures
        t = pd.DataFrame({"group": ["a", "a", "b", "b"], "unit": ["u1", "u2", "u1", "u2"], "value": [1, 2, 3, 4]})
        c = figures.dots(t, "group", "value", "unit", self.path("old_dots"), "dots", "value vs control")
        self.assertEqual(c["layout_issues"], [])
        self.assertEqual(figures.bars(np.ones(3), ["a", "b", "c"], self.path("old_bars"), "bars",
                                      "cells")["layout_issues"], [])
        self.assertEqual((figures._width(3), figures._width(300)), (figures.SINGLE, figures.DOUBLE))
        plt = figures._plt()
        fig, ax = plt.subplots(figsize=(figures.SINGLE, figures.HEIGHT))
        ax.text(0.5, 0.5, "too small", fontsize=3)
        issues = figures._save(plt, fig, self.path("small_text"), "small")
        self.assertTrue(any("below 5 pt" in i for i in issues), issues)

    def test_long_unit_names_are_set_upright_where_they_would_overlap(self):
        """A.what_was_done's figure on a screen whose units are its four hashtags and its cells without one: the
        names overlap at 45 degrees, so they are set upright, and the layout checks pass."""
        from pertura.question_modules import figures
        from pertura.question_modules.a import KINDS
        units = ["GSE278572:unassigned", "Resting_Teff", "Resting_Treg", "Stimulated_Teff", "Stimulated_Treg"]
        values = pd.DataFrame(self.rng.integers(100, 1000, (5, len(KINDS))), index=units, columns=list(KINDS))
        measures = pd.DataFrame({"n_counts": self.rng.lognormal(8, 0.5, 500), "n_genes": self.rng.normal(2000, 300, 500),
                                 "pct_mito": self.rng.uniform(0, 0.1, 500)})
        c = figures.cells_and_quality(values, units, list(KINDS), measures, np.repeat(units, 100),
                                      self.path("cells_and_quality"), "cells")
        self.assertEqual(c["layout_issues"], [])
        plt = figures._plt()
        for names, rotation in ((units * 2, 90.0), (["a", "b", "c"], 45.0)):  # short names keep their slant
            fig, ax = plt.subplots(figsize=(figures.SINGLE, figures.HEIGHT))
            ax.bar(range(len(names)), 1)
            ax.set_xticks(range(len(names)))
            ax.set_xticklabels(names, rotation=45, ha="right")
            fig.tight_layout(pad=0.4)
            figures._upright_where_crowded(fig)
            self.assertEqual({t.get_rotation() for t in ax.get_xticklabels()}, {rotation}, names)
            plt.close(fig)

    def test_guide_abundance(self):
        rows = [("control guides", f"C{i}", int(self.rng.poisson(30))) for i in range(10)]
        rows += [(t, f"{t}_{g}", int(self.rng.poisson(40))) for t in self.targets[:12] for g in (1, 2, 3)]
        t = pd.DataFrame(rows, columns=["target", "guide", "cells"])
        self.drawn("abundance", charts.guide_abundance(t, self.path("abundance"), "abundance", burst={"T00_1"}))

    def test_composition_each_target_beside_its_own_control_cells(self):
        states = ["S01", "S02", "S03", "S04"]
        shares = pd.DataFrame(self.rng.dirichlet(np.ones(4), 12), index=self.targets[:12], columns=states)
        matched = {t: pd.Series(self.rng.dirichlet(np.ones(4)), index=states) for t in self.targets[:12]}
        names = {s: f"{s} Gzmb Prf1" for s in states}
        self.drawn("composition_matched", charts.composition(shares, states, self.path("composition_matched"),
                                                             "composition", names=names, matched=matched))

    def test_volcano(self):
        t = pd.DataFrame({"gene": [f"g{i}" for i in range(500)], "effect": self.rng.normal(0, 1, 500),
                          "q": self.rng.uniform(0, 1, 500) ** 3})
        self.drawn("volcano", charts.volcano(t, self.path("volcano"), "volcano"))

    def test_heatmap_clustered_with_marks(self):
        M = self.rng.normal(0, 1, (20, 25))
        marks = np.where(np.abs(M) > 1.8, "*", "")
        self.drawn("heatmap", charts.heatmap(M, self.targets[:20], [f"g{j}" for j in range(25)],
                                             self.path("heatmap"), "heatmap", "log2 FC vs control cells",
                                             marks=marks))

    def test_embedding_and_markers(self):
        xy = self.rng.normal(0, 1, (900, 2)) + np.repeat(np.eye(3)[:, :2] * 4, 300, axis=0)
        states = np.repeat(["S01", "S02", "S03"], 300)
        names = {"S01": "S01 Gzmb Prf1", "S02": "S02 Tcf7 Sell", "S03": "S03 Mki67 Top2a"}
        self.drawn("embedding", charts.embedding_states(xy, states, names, self.path("embedding"), "embedding"))
        rows = [(s, g, self.rng.uniform(0.1, 0.9), self.rng.uniform(0, 3)) for s in names for g in
                ("Gzmb", "Prf1", "Tcf7", "Sell", "Mki67", "Top2a")]
        t = pd.DataFrame(rows, columns=["state", "gene", "fraction", "mean_expression"])
        self.drawn("markers", charts.marker_dots(t, self.path("markers"), "markers"))
        on = states == "S01"
        self.drawn("density", charts.density_difference(xy, on, ~on, self.path("density"), "density",
                                                        "Density, target − control"))
        self.assertEqual(charts.state_colours(["S02", "S01"])["S01"], charts.state_colours(["S01", "S03"])["S01"])

    def test_marker_table_measures_every_gene_in_every_state(self):
        """C.states' dot plot table: each state's markers measured in every state. A gene measured only in its own
        state had no scaled colour, and its dot was not drawn."""
        from types import SimpleNamespace
        from pertura.question_modules.c import marker_table
        lab = np.repeat(["S01", "S02", "S03"], 50)
        a = SimpleNamespace(var_names=pd.Index([f"g{j}" for j in range(5)]),
                            X=self.rng.poisson(1.0, (150, 5)).astype(float))
        markers = pd.DataFrame({"state": ["S01", "S01", "S02", "S03", "S03"],
                                "gene": ["g0", "g1", "g2", "g3", "absent"]})
        names = {s: f"{s} g" for s in ("S01", "S02", "S03")}
        t = marker_table(a, lab, names, markers)
        self.assertEqual(len(t), 4 * 3)
        self.assertEqual(set(t.groupby("gene")["state"].nunique()), {3})
        self.drawn("markers_module", charts.marker_dots(t, self.path("markers_module"), "markers",
                                                        states_order=list(names.values())))

    def test_panels_of_a_composed_figure(self):
        """The design, the effects coloured by mode, the targets' integrated panel and a target's cells on the map,
        drawn alone and as the lettered panels of one figure 183 mm wide, without layout issues."""
        from pertura.question_modules import figure_series
        stages = [("Library", ["19 genes, CRISPRi and CRISPRa", "2 guides per target"]), ("Cells", ["K562"]),
                  ("Samples", ["5 channels"]), ("Readout", ["10x 3' scRNA-seq", "direct guide capture"])]
        t = pd.DataFrame({"target": self.targets, "knock": self.rng.normal(-0.8, 0.6, 30),
                          "dist": self.rng.gamma(2, 0.05, 30), "mode": ["CRISPRi", "CRISPRa"] * 15})
        P = self.rng.normal(0, 1, (30, 60))
        C = pd.DataFrame(np.corrcoef(P), index=self.targets, columns=self.targets)
        C.iloc[0, 5] = C.iloc[5, 0] = np.nan  # a pair with no sample in common
        side = pd.DataFrame({"Cells": self.rng.integers(100, 800, 30), "Score": self.rng.normal(0, 0.1, 30)},
                            index=self.targets)
        xy = self.rng.normal(0, 1, (2000, 2))
        groups = self.rng.choice(self.targets[:3] + ["control"], 2000)
        self.drawn("design", charts.design_panel(stages, self.path("design")))
        self.drawn("target_panel", charts.target_panel(C, side, self.path("target_panel")))
        self.drawn("highlight", charts.embedding_highlight(xy, groups, self.targets[:2], self.path("highlight"),
                                                            states=np.where(xy[:, 0] > 0, "S01", "S02")))
        rows = [(28, [(1, lambda p: charts.design_panel(stages, fig=p))]),
                (62, [(1, lambda p: charts.two_scores(t, "knock", "dist", None, "", "Target gene, log2 FC",
                                                      "E-distance", group="mode", group_order=["CRISPRi", "CRISPRa"],
                                                      fig=p)),
                      (1, lambda p: charts.embedding_highlight(xy, groups, self.targets[:2], fig=p))]),
                (80, [(1, lambda p: charts.target_panel(C, side, fig=p))])]
        issues, letters = figure_series.compose(rows, self.path("composed"), "composed")
        self.assertEqual(letters, ["a", "b", "c", "d"])
        self.drawn("composed", issues)

    def test_a_result_figure_of_titled_panels_enters_the_record(self):
        """ADR 0039, D5: a result's figure built from panels, a chart's and one's own drawing, each with its letter
        and, where given, its title above it: one figure 183 mm wide that passes the layout checks and save_figure's
        standard."""
        import time
        from pertura.question_modules import figure_series, figures
        M = np.corrcoef(self.rng.normal(0, 1, (12, 40)))
        marks = np.where(np.abs(M) > 0.3, "*", "")
        genes = pd.DataFrame({"gene": [f"G{i}" for i in range(500)], "effect": self.rng.normal(0, 1, 500),
                              "q": self.rng.uniform(0, 1, 500) ** 3})

        def own(p):
            ax = p.add_subplot()
            ax.scatter(range(6), self.rng.normal(0, 1, 6), s=8, color="0.3", linewidths=0)
            ax.set_ylabel("Share minus control share")
        rows = [(60, [(1.2, lambda p: charts.heatmap(M, self.targets[:12], self.targets[:12], None, "",
                                                    "Correlation of two targets' profiles", marks=marks, fig=p),
                       "The first six targets change expression alike, beyond all control-guide pairs"),
                      (1, lambda p: charts.volcano(genes, None, "", fig=p), "Their shared genes")]),
                (45, [(1, own), (1, own, "Each sample's share of the cycling state")])]
        start = time.time()
        fig, letters = figure_series.figure(rows)
        self.assertEqual(letters, ["a", "b", "c", "d"])
        self.assertEqual(figures.check_figure(fig), [])
        self.assertAlmostEqual(fig.get_size_inches()[0] * 25.4, 183, delta=1)
        entry = figures.save_figure(fig, "result_1", pd.DataFrame({"x": [1]}), "A result's figure", "overview",
                                    folder=self.tmp.name, strict=True)
        self.assertEqual(entry["checks"], [])
        self.assertLess(time.time() - start, 20)

    def test_marks_on_a_wide_heatmap_are_dots(self):
        """Fifty columns leave no room for a mark's text in a cell: the marks are dots, and nothing overlaps."""
        M = self.rng.normal(0, 1, (8, 50))
        marks = np.where(np.abs(M) > 1, "**", "")
        self.drawn("wide_marks", charts.heatmap(M, [f"S{i}" for i in range(8)], [f"T{i:02d}" for i in range(50)],
                                                self.path("wide_marks"), "wide", "Share vs control cells",
                                                marks=marks, cap=98))

    def test_many_states_carry_their_ids_and_a_legend(self):
        centres = self.rng.normal(0, 6, (16, 2))
        xy = np.repeat(centres, 60, axis=0) + self.rng.normal(0, 1, (960, 2))
        states = np.repeat([f"S{i:02d}" for i in range(16)], 60)
        names = {f"S{i:02d}": f"S{i:02d} SELENOM COL6A2" for i in range(16)}
        self.drawn("embedding_many", charts.embedding_states(xy, states, names, self.path("embedding_many"),
                                                             "embedding"))

    def test_two_scores_and_signature_by_state(self):
        t = pd.DataFrame({"target": self.targets, "erythroid": self.rng.normal(0, 1, 30),
                          "myeloid": self.rng.normal(0, 1, 30)})
        self.drawn("scores", charts.two_scores(t, "erythroid", "myeloid", self.path("scores"), "scores",
                                               "Erythroid score vs control cells", "Myeloid score vs control cells"))
        rows = [(tg, s, self.rng.normal(0, 0.5), self.rng.uniform(0, 0.2)) for tg in self.targets[:15]
                for s in ("S01", "S02", "S03")]
        sig = pd.DataFrame(rows, columns=["target", "state", "change", "q"])
        self.drawn("signature", charts.signature_by_state(sig, self.path("signature"), "signature",
                                                          "Score vs control cells, within the state"))

    def test_one_lists_enrichment(self):
        names = [f"HALLMARK_SET_NUMBER_{i}" for i in range(19)] + ["HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION"]
        t = pd.DataFrame({"set": names, "overlap": self.rng.integers(5, 32, 20),
                          "q": 10 ** -self.rng.uniform(1.5, 16, 20)})
        self.drawn("enrichment", charts.enrichment_dots(t, 448, self.path("enrichment"), "enrichment"))

    def test_two_lists_one_with_no_hit(self):
        # an empty list's table concatenated with the other's leaves the columns as objects (development session 3:
        # G.meaning failed in matplotlib, "s must be float")
        up = pd.DataFrame({"set": [f"HALLMARK_SET_{i}" for i in range(6)], "overlap": self.rng.integers(3, 20, 6),
                           "q": 10 ** -self.rng.uniform(1, 6, 6), "group": "up"})
        down = pd.DataFrame(columns=["set", "overlap", "q", "group"])
        t = pd.concat([up, down], ignore_index=True).astype({"overlap": object})
        self.drawn("enrichment_two", charts.enrichment_dots(t, {"up": 677, "down": 355},
                                                            self.path("enrichment_two"), "enrichment"))

    def test_pathways_knockdown_and_pseudotime(self):
        rows = [(f"HALLMARK_SET_{i}", g, self.rng.normal(0, 2), self.rng.uniform(0, 0.1) ** 2) for i in range(25)
                for g in ("T00", "T01", "T02")]
        t = pd.DataFrame(rows, columns=["set", "group", "score", "q"])
        self.drawn("pathways", charts.pathway_dots(t, self.path("pathways"), "pathways"))
        k = pd.DataFrame({"guide": [f"T{i:02d}_1" for i in range(30)], "baseline": self.rng.lognormal(0, 1, 30),
                          "log2fc": self.rng.normal(-1, 0.6, 30), "past_the_line": self.rng.random(30) > 0.2})
        self.drawn("knockdown", charts.knockdown_vs_baseline(k, self.path("knockdown"), "knockdown"))
        pt = self.rng.uniform(0, 1, 900)
        groups = np.repeat(["control", "T00", "T01"], 300)
        self.drawn("pseudotime", charts.pseudotime_densities(pt, groups, self.path("pseudotime"), "pseudotime"))

    def test_a_share_is_drawn_on_its_own_range(self):
        """Every draw called again (shares of 1) does not make an axis from 0.96 to 1.04."""
        from pertura.question_modules import figures
        plt = figures._plt()
        t = pd.DataFrame({"cells": ["40 cells"] * 3 + ["100 cells"] * 3, "share": [1.0] * 6})
        fig = plt.figure()
        self.assertEqual(charts.strips(t, "share", "cells", None, "share", fig=fig, xlim=(0, 1.02)), [])
        self.assertEqual(fig.axes[0].get_xlim(), (0, 1.02))
        plt.close(fig)

    def test_pseudotime_densities_one_width_and_the_bulk_on_the_axis(self):
        """A group of 300 cells drawn from the control cells' distribution peaks as high as the 20,000 control cells,
        and 0.2% far cells do not stretch the axis."""
        from pertura.question_modules import figures
        plt = figures._plt()
        def sample(n):
            return np.where(self.rng.random(n) < 0.5, self.rng.normal(0.05, 0.01, n), self.rng.normal(0.3, 0.01, n))
        pt = np.concatenate([sample(20000), np.full(40, 1.0), sample(300)])
        groups = np.array(["control"] * 20040 + ["T00"] * 300)
        fig = plt.figure()
        self.assertEqual(charts.pseudotime_densities(pt, groups, None, "", fig=fig), [])
        control, target = (line.get_ydata() for line in fig.axes[0].lines)
        self.assertLess(abs(target.max() / control.max() - 1), 0.25)
        self.assertLess(fig.axes[0].lines[0].get_xdata().max(), 0.5)
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
