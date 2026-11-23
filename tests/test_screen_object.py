"""The screen object (pertura.screen_object): call rules, sets, and a build from a small deposit."""
import gzip
import json
import os
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import scipy.io
import scipy.sparse as sp

from pertura import screen_object as so


def node(v):
    return {"value": v, "status": "proposed"}


TABLE = {"A_1": ("A", "targeting"), "A_2": ("A", "targeting"), "B_1": ("B", "targeting"),
         "NT_1": ("NT", "non_targeting")}


def test_dominance_rule_with_and_without_a_lone_target_minimum():
    seen = [[("A_1", 2), ("A_2", 2)], [("A_1", 2)], [("A_1", 9), ("B_1", 2)], [("A_1", 4), ("B_1", 3)], []]
    strict, _ = so.label_cells(seen, TABLE, {"rule": "dominance", "min_umis": 3, "ratio": 3})
    assert strict == ["A", "ambiguous", "A", "ambiguous", "none"]  # guides of one target summed: 2 + 2 = 4
    lone, _ = so.label_cells(seen, TABLE, {"rule": "dominance", "min_umis": 3, "ratio": 3, "one_target": "any"})
    assert lone[1] == "A"


def test_deposit_rule_and_matrix_weights_for_several_targets():
    seen = [[("A_1", 5)], [("A_1", 5), ("B_1", 4)]]
    dep, calls = so.label_cells(seen, TABLE, {"rule": "deposit"})
    assert dep == ["A", "multi"] and calls[1] == "A_1:5;B_1:4"
    weights = [[("A_1", 5)], [("A_1", 30), ("A_2", 10), ("B_1", 4)]]
    w, _ = so.label_cells(seen, TABLE, {"rule": "dominance", "umis_from": "matrix", "one_target": "any"}, None,
                          weights)
    assert w == ["A", "A"]


def test_sets_label_whole_partial_and_two_set_cells():
    table = {g: (g.split("_")[0], "targeting") for g in ("m_1", "t_1", "e_1", "f_1")}
    seen = [[("m_1", 5), ("t_1", 5)], [("m_1", 5)], [("m_1", 5), ("t_1", 5), ("e_1", 5), ("f_1", 5)]]
    lab, _ = so.label_cells(seen, table, {"rule": "deposit"}, sets=[["m", "t"], ["e", "f"]])
    assert lab == ["m+t", "incomplete", "two sets"]


def test_guide_table_from_map_controls_and_reference():
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"]}),
                               "controls": node([{"kind": "safe_targeting", "guides": ["NT_1"]}])}}
    t = so.guide_table(design, ["A_1", "NT_1", "X_9"])
    assert t == {"A_1": ("A", "targeting"), "NT_1": ("NT_1", "safe_targeting"), "X_9": ("X_9", "unknown")}
    assert so.control_groups(t) == {"NT_1": "safe_targeting"}
    ref = {"perturbation": {"target_map": node("feature_reference"), "controls": node([])}}
    assert so.guide_table(ref, ["A_1"], {"A_1": "A"})["A_1"] == ("A", "targeting")


def _deposit(tmp_path, antibodies=()):
    d = tmp_path / "dep"
    d.mkdir()
    rng = np.random.default_rng(0)
    genes = [f"G{i}" for i in range(50)]
    guides = ["A_1", "A_2", "B_1", "NT_1"]
    n = 60
    X = rng.poisson(3, (n, 50))
    Gm = np.zeros((n, 4), int)
    Gm[:20, 0], Gm[:20, 1] = 4, 3      # A by two guides
    Gm[20:40, 2] = 6                    # B
    Gm[40:50, 3] = 5                    # control
    Gm[50:55, 0], Gm[50:55, 2] = 4, 4   # ambiguous
    Ab = rng.poisson(40, (n, len(antibodies)))  # CITE-seq: each cell's antibody counts
    M = sp.coo_matrix(np.hstack([X, Gm, Ab]).T)
    with gzip.open(d / "s1_matrix.mtx.gz", "wb") as fh:
        scipy.io.mmwrite(fh, M)
    feats = ([f"{g}\t{g}\tGene Expression" for g in genes] + [f"{g}\t{g}\tCRISPR Guide Capture" for g in guides]
             + [f"{p}\t{p}\tAntibody Capture" for p in antibodies])
    (d / "s1_features.tsv").write_text("\n".join(feats) + "\n")
    (d / "s1_barcodes.tsv").write_text("\n".join(f"C{i:03d}-1" for i in range(n)) + "\n")
    return d


def test_build_writes_cells_guides_and_summary(tmp_path):
    d = _deposit(tmp_path)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    out = tmp_path / "screen"
    s = so.build(str(d), design, str(out))
    assert s["labels"] == {"A": 20, "B": 20, "control": 10, "ambiguous": 5, "none": 5}
    cells = pd.read_csv(out / "cells.tsv.gz", sep="\t")
    assert set(cells.loc[cells["label"] == "control", "control_kind"]) == {"non_targeting"}
    assert set(cells.loc[cells["label"] == "control", "control_group"]) == {"NT_1"}
    g = pd.read_csv(out / "guides.tsv", sep="\t").set_index("guide")
    assert g.loc["A_1", "cells"] == 20 and g.loc["NT_1", "kind"] == "non_targeting"
    assert json.load(open(out / "screen.json"))["schema"] == so.SCHEMA


def test_the_parsed_matrices_build_the_same_object(tmp_path):
    d = _deposit(tmp_path)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    plain, kept = tmp_path / "plain", tmp_path / "kept"
    so.build(str(d), design, str(plain))
    parsed = tmp_path / "parsed"
    s = so.build(str(d), design, str(kept), parsed=str(parsed))
    assert len(list(parsed.glob("*.h5ad"))) == 1 and s["parsed"] == str(parsed.resolve())
    a, b = (pd.read_csv(x / "cells.tsv.gz", sep="\t") for x in (plain, kept))
    pd.testing.assert_frame_equal(a, b)
    ca, cb = so.load(str(plain), cache=False), so.load(str(kept), cache=False)
    assert (ca.X != cb.X).nnz == 0 and list(ca.var_names) == list(cb.var_names)


def test_the_antibody_counts_stay_beside_the_genes(tmp_path):
    """Session 10: the build kept the genes alone, so CD25's protein never reached the agent."""
    d = _deposit(tmp_path, antibodies=("CD25", "CD127", "HTO_1"))
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    out = tmp_path / "screen"
    s = so.build(str(d), design, str(out))
    assert s["antibody_features"] == ["CD25", "CD127", "HTO_1"]
    for a in (so.load(str(out)), so.load(str(out), cache=False)):
        ab = a.obsm["antibody_counts"]
        assert list(ab.columns) == ["CD25", "CD127", "HTO_1"] and list(ab.index) == list(a.obs_names)
        assert int(ab.to_numpy().sum()) > 0 and a.shape[1] == 50  # the genes alone in X
    other, plain = tmp_path / "no_antibodies", tmp_path / "plain"
    other.mkdir()
    assert "antibody_features" not in so.build(str(_deposit(other)), design, str(plain))
    assert "antibody_counts" not in so.load(str(plain)).obsm


def test_a_rebuild_that_finds_the_counts_cached_still_names_the_antibodies(tmp_path):
    """GSE263761 on 0adebaa: the build after the design changed read the counts the first build had cached, and its
    summary lost the 11 antibodies and where the counts came from, so no reply told the agent there were proteins."""
    d = _deposit(tmp_path, antibodies=("CD25", "CD127", "HTO_1"))
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    out = tmp_path / "screen"
    first = so.build(str(d), design, str(out))
    again = so.build(str(d), {**design, "units": {"independent_unit": node("culture")}}, str(out))  # same cells
    assert again["antibody_features"] == first["antibody_features"] == ["CD25", "CD127", "HTO_1"]
    assert again["counts_from"] == first["counts_from"] == "the deposit's count matrices"
    assert so.load(str(out)).uns == {}


def test_load_reads_counts_once_and_the_cells_always(tmp_path):
    d = _deposit(tmp_path)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    out = tmp_path / "screen"
    so.build(str(d), design, str(out))
    # the counts are cached before the summary: whoever finds screen.json finds the counts and only reads them
    counts = list(out.glob("counts-*.h5ad"))
    assert counts and counts[0].stat().st_mtime <= (out / "screen.json").stat().st_mtime
    with mock.patch("pertura.design_audit.readers.read_matrix", side_effect=AssertionError("assembled again")):
        a = so.load(str(out))
    assert a.shape == (60, 50) and int(a.X.sum()) > 0
    assert list(out.glob("counts-*.h5ad")) and not list(out.glob("*.tmp"))
    cells = pd.read_csv(out / "cells.tsv.gz", sep="\t", keep_default_na=False)
    cells["label"] = cells["label"].replace({"B": "B renamed"})  # a rebuild with new labels and the same cells
    cells.to_csv(out / "cells.tsv.gz", sep="\t", index=False)
    b = so.load(str(out))
    assert (b.X != a.X).nnz == 0
    assert (b.obs["label"] == "B renamed").sum() == 20 and (b.obs["control_group"] == "").sum() == 50


def test_a_controls_name_makes_the_deposits_own_control_label_control(tmp_path):
    d = _deposit(tmp_path)
    cond = ["A"] * 20 + ["B"] * 20 + ["Non-Targeting"] * 10 + ["none"] * 10
    pd.DataFrame({"cell_barcode": [f"C{i:03d}-1" for i in range(60)], "condition_label": cond}).to_csv(
        d / "cell_metadata.csv", index=False)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"name": "Non-Targeting", "kind": "non_targeting", "guides": []}]),
                               "assignment": node({"rule": "labels", "column": "condition_label"})},
              "units": {"independent_unit": node("mouse")}}
    s = so.build(str(d), design, str(tmp_path / "screen"))
    assert s["labels"]["control"] == 10 and "Non-Targeting" not in s["labels"]
    cells = pd.read_csv(tmp_path / "screen" / "cells.tsv.gz", sep="\t")
    assert set(cells.loc[cells["label"] == "control", "control_group"]) == {"Non-Targeting"}


def test_confirmed_controls_that_match_no_cell_stop_the_build(tmp_path):
    """GSE203592 on 6a87acd: the build read the confirmed control CTRL1 as a tenth target with 0 control cells, said
    only as one reading check in a 16 KB reply, and the first look ran on it."""
    d = _deposit(tmp_path)
    cond = ["A"] * 20 + ["B"] * 20 + ["CTRL"] * 10 + ["none"] * 10
    pd.DataFrame({"cell_barcode": [f"C{i:03d}-1" for i in range(60)], "condition_label": cond}).to_csv(
        d / "cell_metadata.csv", index=False)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"name": "CTRL1", "kind": "safe_targeting", "guides": ["CTRL1-1"]}]),
                               "assignment": node({"rule": "labels", "column": "condition_label"})},
              "units": {"independent_unit": node("mouse")}}
    with pytest.raises(ValueError, match=r"the confirmed controls \(CTRL1\) match no cell.*CTRL \(10\)"):
        so.build(str(d), design, str(tmp_path / "screen"))


def test_every_design_key_the_build_reads_is_proposed_or_asked_somewhere():
    """perturbation.control_labels was read by the build and named by no form, check or auditor's line: the CTRL1
    build was confirmed without it. Each key the build reads is named where the auditor reads (its file, the
    measurement's questions) or on the form."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    read = set(re.findall(r'_value\(design, "([a-z_.]+)"\)', open(so.__file__, encoding="utf-8").read()))
    named = "".join(open(os.path.join(root, *p.split("/")), encoding="utf-8").read() for p in (
        "plugin/agents/design-auditor.md", "src/pertura/design_audit/inventory.py", "src/pertura/design_service.py",
        "src/pertura/design_gate.py"))
    assert read and not sorted(k for k in read if k not in named)


def test_a_controls_own_name_is_its_label_under_the_labels_rule(tmp_path):
    """GSE203592 on 6a87acd: the controls confirmed as {name: CTRL1, guides: CTRL1-1..12}, the cells labelled CTRL1
    by the authors, and the build read CTRL1 as a tenth target with 0 control cells."""
    d = _deposit(tmp_path)
    cond = ["A"] * 20 + ["B"] * 20 + ["CTRL1"] * 10 + ["none"] * 10
    pd.DataFrame({"cell_barcode": [f"C{i:03d}-1" for i in range(60)], "condition_label": cond}).to_csv(
        d / "cell_metadata.csv", index=False)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"name": "CTRL1", "kind": "safe_targeting",
                                                  "guides": ["CTRL1-1", "CTRL1-2"]}]),
                               "assignment": node({"rule": "labels", "column": "condition_label"})},
              "units": {"independent_unit": node("mouse")}}
    s = so.build(str(d), design, str(tmp_path / "screen"))
    assert s["labels"]["control"] == 10 and "CTRL1" not in s["labels"]
    cells = pd.read_csv(tmp_path / "screen" / "cells.tsv.gz", sep="\t")
    assert set(cells.loc[cells["label"] == "control", "control_kind"]) == {"safe_targeting"}


def test_a_deposits_own_word_for_cells_of_no_target_holds_no_target_under_the_labels_rule(tmp_path):
    """GSE203592 on 308d820: the authors' 'zMulti' and 'zNone' were read as two targets (11 of 11 past their line, a
    set alike); the reading check listed them and nothing acted on it."""
    d = _deposit(tmp_path)
    cond = ["A"] * 20 + ["B"] * 20 + ["Non-Targeting"] * 10 + ["zMulti"] * 6 + ["zNone"] * 4
    pd.DataFrame({"cell_barcode": [f"C{i:03d}-1" for i in range(60)], "condition_label": cond}).to_csv(
        d / "cell_metadata.csv", index=False)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"name": "Non-Targeting", "kind": "non_targeting", "guides": []}]),
                               "assignment": node({"rule": "labels", "column": "condition_label"})},
              "units": {"independent_unit": node("mouse")}}
    s = so.build(str(d), design, str(tmp_path / "screen"))
    assert s["labels"]["ambiguous"] == 10 and not {"zMulti", "zNone"} & set(s["labels"])
    said = next(x for x in s["reading_checks"] if x["check"] == "labels outside the design")
    assert said["labels"] == ["zMulti", "zNone"] and said["read_as"] == "ambiguous"


def test_labels_spelt_otherwise_than_every_target_stay_as_read(tmp_path):
    """Labels such as 'A_KO' against the target 'A' share no name with the design's targets: nothing is read as cells
    of no target, and both reading checks name the mismatch."""
    d = _deposit(tmp_path)
    cond = ["A_KO"] * 20 + ["B_KO"] * 20 + ["Non-Targeting"] * 10 + ["zNone"] * 10
    pd.DataFrame({"cell_barcode": [f"C{i:03d}-1" for i in range(60)], "condition_label": cond}).to_csv(
        d / "cell_metadata.csv", index=False)
    design = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                               "controls": node([{"name": "Non-Targeting", "kind": "non_targeting", "guides": []}]),
                               "assignment": node({"rule": "labels", "column": "condition_label"})},
              "units": {"independent_unit": node("mouse")}}
    s = so.build(str(d), design, str(tmp_path / "screen"))
    assert s["labels"]["A_KO"] == 20 and "ambiguous" not in s["labels"]
    checks = {x["check"]: x for x in s["reading_checks"]}
    assert checks["library targets without cells"]["targets"] == ["A", "B"]
    assert "read_as" not in checks["labels outside the design"]


def test_the_combination_rule_labels_every_target_with_enough_umis():
    table = {"m_1": ("m", "targeting"), "t_1": ("t", "targeting"), "t_2": ("t", "targeting"),
             "C_1": ("C", "non_targeting")}
    seen = [[("m_1", 5), ("t_1", 4)], [("m_1", 30), ("t_1", 2)], [("t_1", 1), ("t_2", 1)], [("m_1", 1)], [],
            [("C_1", 6)]]
    lab, _ = so.label_cells(seen, table, {"rule": "combination", "min_umis": 2, "min_share": 0.1})
    # t's 2 UMIs are under 10% of 32 in the second cell; its two guides sum to 2 in the third
    assert lab == ["m+t", "m", "t", "none", "none", "C"]


import unittest  # noqa: E402


class TestPerGuideThreshold(unittest.TestCase):
    def test_the_mixture_threshold_separates_background_from_real_calls(self):
        import numpy as np
        rng = np.random.default_rng(0)
        background = rng.poisson(1.5, 400) + 1
        real = rng.poisson(40, 200) + 1
        t = so.guide_thresholds([[("g", int(u))] for u in np.r_[background, real]])["g"]
        self.assertGreater(t, 4)
        self.assertLess(t, 30)
        self.assertEqual(so.guide_thresholds([[("g", 1)]] * 10)["g"], 2)  # cannot fit: min_umis

    def test_two_guides_of_one_control_group_label_one_control_cell(self):
        table = {"scr_1": ("scramble", "non_targeting"), "scr_2": ("scramble", "non_targeting"),
                 "a_1": ("A", "targeting"), "b_1": ("B", "targeting")}
        seen = [[("scr_1", 30), ("scr_2", 25)], [("a_1", 30), ("b_1", 40)], [("a_1", 30), ("b_1", 1)], []]
        seen += [[("a_1", 1)], [("b_1", 1)], [("scr_1", 1)], [("scr_2", 1)]] * 30
        labels, _ = so.label_cells(seen, table, {"rule": "per_guide_threshold", "min_umis": 2})
        self.assertEqual(labels[:4], ["scramble", "multi", "A", "none"])


def _mixture_runs():
    """Whether scikit-learn's Gaussian mixture runs here (its threadpoolctl fails on some Windows installs)."""
    try:
        import numpy as np
        from sklearn import mixture
        mixture.GaussianMixture(2, random_state=0).fit(np.r_[np.zeros(5), np.ones(5)].reshape(-1, 1))
        return True
    except Exception:
        return False


@unittest.skipUnless(_mixture_runs(), "scikit-learn's Gaussian mixture does not run in this environment")
class TestCellRangerRule(unittest.TestCase):
    """The "cellranger" rule follows Cell Ranger's call_presence_with_gmm_ab (cellranger/feature/feature_assigner.py,
    releases 7.2.0 to 10.0.0): a tied two-component mixture on log10(1 + UMIs) over every cell of a run."""

    def seen(self, seed=0):
        import numpy as np
        rng = np.random.default_rng(seed)
        umis = np.r_[np.zeros(300, int), rng.poisson(1.0, 300), rng.poisson(30, 150) + 5]
        return [[("g", int(u))] if u > 0 else [] for u in umis], umis

    def test_it_matches_cell_rangers_computation(self):
        import numpy as np
        from sklearn import mixture
        seen, umis = self.seen()
        lx = np.log10(1 + umis.astype(float)).reshape(-1, 1)
        gmm = mixture.GaussianMixture(n_components=2, n_init=10, covariance_type="tied", random_state=0).fit(lx)
        called = gmm.predict(lx) == int(np.argmax(gmm.means_))
        t = so.cellranger_thresholds(seen)[("all", "g")]
        self.assertEqual(t, float(umis[called].min()))
        labels, _ = so.label_cells(seen, {"g": ("G", "targeting")}, {"rule": "cellranger"})
        self.assertEqual([lab == "G" for lab in labels], list(called))  # one boundary: every cell above it called

    def test_each_run_is_fitted_apart_and_the_floor_applies(self):
        seen, _ = self.seen()
        quiet = [[("g", 1)] for _ in range(200)] + [[("g", 4)] for _ in range(20)]
        both = seen + quiet
        runs = ["r1"] * len(seen) + ["r2"] * len(quiet)
        t = so.cellranger_thresholds(both, runs)
        self.assertNotEqual(t[("r1", "g")], t[("r2", "g")])
        self.assertEqual(so.cellranger_thresholds(quiet, min_umis=5)[("all", "g")], None)  # 4 UMIs < the floor

    def test_a_floor_after_the_fit_calls_as_the_fit_with_the_floor(self):
        """One fit serves every floor (the guide-calling preview, 2026-11-11). On GSE263761, 955 of 5,391 guides of a
        sample had a threshold of 1, guides with ambient UMIs alone, so most cells were called with several guides;
        a floor of 3 called them by their own guide. The fit with the floor and the floor after the fit agree."""
        import numpy as np
        rng = np.random.default_rng(0)
        n = 1000
        g = np.zeros(n, int)
        g[:50] = rng.poisson(150, 50)  # the guide a cell carries
        h = np.zeros(n, int)
        h[rng.choice(n, 350, replace=False)] = rng.choice([1, 1, 1, 2], 350)  # a guide seen only as ambient UMIs
        seen = [[(name, int(u)) for name, u in (("g", a), ("h", b)) if u > 0] for a, b in zip(g, h)]
        table = {"g": ("G", "targeting"), "h": ("H", "targeting")}
        direct = so.cellranger_thresholds(seen, min_umis=3)
        expected = []
        for v in seen:
            over = {table[x][0] for x, u in v if direct[("all", x)] is not None and u >= direct[("all", x)]}
            expected.append("none" if not over else over.pop() if len(over) == 1 else "multi")
        labels, _ = so.label_cells(seen, table, {"rule": "cellranger", "min_umis": 3})
        self.assertEqual(labels, expected)
        no_floor = so.label_cells(seen, table, {"rule": "cellranger"})[0]
        self.assertGreater(labels.count("G"), no_floor.count("G"))  # the ambient guide no longer makes cells several

    def test_kept_thresholds_only_without_a_fit(self):
        import tempfile
        seen, _ = self.seen()
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(so.cellranger_thresholds(seen, cache=d, fit=False))  # nothing kept: no fit, None
            kept = so.cellranger_thresholds(seen, cache=d)
            self.assertEqual(so.cellranger_thresholds(seen, cache=d, fit=False), kept)

    def test_a_later_call_on_the_same_cells_reads_the_kept_thresholds(self):
        import os
        import tempfile
        seen, _ = self.seen()
        two = [v + [("h", 3)] for v in seen]  # two guides in a cell: their order in it is not what the fits read
        runs = ["r1"] * len(two)
        with tempfile.TemporaryDirectory() as d:
            first = so.cellranger_thresholds(two, runs, cache=d)
            self.assertEqual(first, so.cellranger_thresholds(two, runs))  # kept or not, the same thresholds
            with mock.patch("sklearn.mixture.GaussianMixture", side_effect=AssertionError("fitted again")):
                self.assertEqual(so.cellranger_thresholds([v[::-1] for v in two], runs, cache=d), first)
                labels, _ = so.label_cells(two, {"g": ("G", "targeting"), "h": ("H", "targeting")},
                                           {"rule": "cellranger"}, groups=runs, cache=d)
                with self.assertRaises(AssertionError):  # the cells in another order: fitted again
                    so.cellranger_thresholds(two[1:] + two[:1], runs, cache=d)
            self.assertEqual(len(labels), len(two))
            self.assertEqual([f for f in os.listdir(d) if not f.endswith(".json")], [])  # no half-written file


class TestAProcessedObjectsCounts(unittest.TestCase):
    """Where a processed object holds its counts, as the measurement reads a matrix, and said in the summary."""

    def objects(self):
        import anndata as ad
        rng = np.random.default_rng(0)
        counts = rng.poisson(1.5, (60, 12)).astype(np.float32)
        logged = np.log1p(counts)
        normalised = np.log1p(counts / np.maximum(counts.sum(1, keepdims=True), 1) * 1e4)
        return ad, counts, logged, normalised

    def test_the_first_source_that_holds_counts(self):
        ad, counts, logged, normalised = self.objects()
        self.assertEqual(so.counts_source(ad.AnnData(counts))[:2], ("X", False))
        with_layer = ad.AnnData(logged, layers={"counts": counts})
        self.assertEqual(so.counts_source(with_layer)[:2], ("counts", False))
        with_raw = ad.AnnData(logged)
        with_raw.raw = ad.AnnData(counts)
        self.assertEqual(so.counts_source(with_raw)[:2], ("raw", False))
        where, read_back, words = so.counts_source(ad.AnnData(logged))
        self.assertEqual((where, read_back), ("X", True))
        self.assertIn("log(1 + counts)", words)
        where, read_back, words = so.counts_source(ad.AnnData(normalised))
        self.assertEqual((where, read_back), ("X", True))
        self.assertIn("not counts", words)

    def test_the_build_reads_the_layer_and_says_where_from(self):
        import os
        import tempfile
        ad, counts, logged, _ = self.objects()
        with tempfile.TemporaryDirectory() as d:
            deposit, screen = os.path.join(d, "deposit"), os.path.join(d, "screen")
            os.makedirs(deposit)
            os.makedirs(screen)
            names = [f"c{i}" for i in range(len(counts))]
            ad.AnnData(logged, obs=pd.DataFrame(index=names), var=pd.DataFrame(index=[f"g{j}" for j in range(12)]),
                       layers={"counts": counts}).write_h5ad(os.path.join(deposit, "object.h5ad"))
            pd.DataFrame({"barcode": names, "label": "control"}).to_csv(os.path.join(screen, "cells.tsv.gz"),
                                                                        sep="	", index=False)
            meta = {"deposit": deposit, "cells_sha256": "x", "guide_features": []}
            a = so.load(screen, meta=meta)
        self.assertEqual(abs(sp.csr_matrix(a.X) - sp.csr_matrix(counts)).max(), 0)  # the layer, not log values
        self.assertIn("layer 'counts'", meta["counts_from"])
