"""Tests for the design auditor's readers and detectors (pertura.design_audit), on a synthetic deposit: two samples
as matrix/features/barcodes triplets with Cell Ranger call files and a feature reference; 4 targets x 2 guides and 6
non-targeting guides; 3 hashtags (declared 'Custom') and 5 surface proteins (declared 'Antibody Capture'); two donors
of different sex pooled in both samples; guides that knock their targets down (CRISPRi-like). Format tests build
smaller deposits: a separate guide matrix, call files of one combined matrix, line endings, Cell Ranger configs."""
import gzip
import os
import tempfile
import unittest

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

from pertura.design_audit import detectors as dt
from pertura.design_audit.audit import audit, write_design
from pertura.design_audit.readers import (discover, read_cellranger_config, read_feature_reference, read_matrix,
                                          split_calls)

TARGETS = ["TA", "TB", "TC", "TD"]
GUIDES = [f"{t}_{k}" for t in TARGETS for k in (1, 2)] + [f"NTC_{k}" for k in range(1, 7)]
HASHTAGS = ["HT-1", "HT-2", "HT-3"]
PROTEINS = ["CD3", "CD4", "CD8", "CD45", "CD69"]


def make_deposit(root, n_cells=400, seed=0, hashtags=None, reference=True):
    """hashtags: per sample, its three hashtag names (lanes of one deposit can carry different ones); default the
    same three in both samples. reference: deposit the feature reference (with its target labels)."""
    rng = np.random.default_rng(seed)
    genes = TARGETS + ["XIST", "RPS4Y1"] + [f"G{i}" for i in range(60)]
    rows = []
    for s in ("S1", "S2"):
        tags = (hashtags or {}).get(s, HASHTAGS)
        feats = ([(g, g, "Gene Expression") for g in genes] + [(f"id_{g}", g, "CRISPR Guide Capture") for g in GUIDES]
                 + [(h, h, "Custom") for h in tags] + [(p, p, "Antibody Capture") for p in PROTEINS])
        guide = rng.choice(GUIDES, size=n_cells)
        X = rng.poisson(5, size=(n_cells, len(feats))).astype(float)
        X[:, :len(TARGETS)] = rng.poisson(20, size=(n_cells, len(TARGETS)))
        for i, g in enumerate(guide):            # CRISPRi-like knockdown of the target's own gene
            if not g.startswith("NTC"):
                X[i, TARGETS.index(g.split("_")[0])] = rng.poisson(1)
        female = rng.random(n_cells) < 0.5      # two donors of different sex in every sample
        X[:, len(TARGETS)] = np.where(female, rng.poisson(8, n_cells), 0)
        X[:, len(TARGETS) + 1] = np.where(female, 0, rng.poisson(8, n_cells))
        G0 = len(genes)
        X[:, G0:G0 + len(GUIDES)] = 0
        for i, g in enumerate(guide):
            X[i, G0 + GUIDES.index(g)] = rng.integers(20, 200)
        H0 = G0 + len(GUIDES)
        X[:, H0:H0 + 3] = rng.poisson(1, size=(n_cells, 3))
        X[np.arange(n_cells), H0 + rng.integers(0, 3, n_cells)] += 100   # one hashtag dominates each cell
        X[:, H0 + 3:] = rng.poisson(50, size=(n_cells, len(PROTEINS)))     # proteins co-expressed
        bcs = [f"AAAC{s}{i:05d}-1" for i in range(n_cells)]
        prefix = os.path.join(root, f"GSM00{s[-1]}_{s}_")
        with gzip.open(prefix + "matrix.mtx.gz", "wb") as fh:
            scipy.io.mmwrite(fh, sp.csr_matrix(X.T))
        pd.DataFrame(feats).to_csv(prefix + "features.tsv.gz", sep="\t", header=False, index=False)
        pd.Series(bcs).to_csv(prefix + "barcodes.tsv.gz", header=False, index=False)
        pd.DataFrame({"cell_barcode": bcs, "num_features": 1, "feature_call": guide,
                      "num_umis": [int(X[i, G0 + GUIDES.index(g)]) for i, g in enumerate(guide)]}) \
            .to_csv(os.path.join(root, f"GSM01{s[-1]}_{s}_protospacer_calls_per_cell.csv.gz"), index=False)
        rows.append(guide)
    ref = pd.DataFrame({"id": [f"id_{g}" for g in GUIDES], "name": GUIDES, "read": "R2", "pattern": "(BC)",
                        "sequence": "ACGT", "feature_type": "CRISPR Guide Capture",
                        "target_gene_id": "", "target_gene_name": [("Non-Targeting" if g.startswith("NTC") else
                                                                    g.split("_")[0]) for g in GUIDES]})
    if reference:
        ref.to_csv(os.path.join(root, "GSE0_feature_reference.csv.gz"), index=False)


class TestDesignAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        make_deposit(cls.tmp.name)
        cls.result = audit(cls.tmp.name)
        cls.facts = {f["key"]: f for f in cls.result["facts"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cellranger_folders_are_named_by_their_sample_folder(self):
        # <sample>/filtered_feature_bc_matrix/matrix.mtx.gz (Zhou 2023's extracted archive): every file is named
        # 'matrix', so the sample is the folder above Cell Ranger's own
        with tempfile.TemporaryDirectory() as root:
            for s, n in (("GSM1_S1_B1", 3), ("GSM2_S2_B1", 4)):
                d = os.path.join(root, s, "filtered_feature_bc_matrix")
                os.makedirs(d)
                with gzip.open(os.path.join(d, "matrix.mtx.gz"), "wb") as fh:
                    scipy.io.mmwrite(fh, sp.csr_matrix(np.ones((2, n))))
                pd.DataFrame([("g1", "g1", "Gene Expression"), ("g2", "g2", "Gene Expression")]).to_csv(
                    os.path.join(d, "features.tsv.gz"), sep="	", header=False, index=False)
                pd.Series([f"{s}{i}-1" for i in range(n)]).to_csv(os.path.join(d, "barcodes.tsv.gz"), header=False,
                                                                 index=False)
            d = discover(root)
            self.assertEqual(sorted(x["name"] for x in d["samples"]), ["S1_B1", "S2_B1"])
            self.assertEqual(d["layout"], "per_sample_mtx")

    def test_discover_pairs_matrices_with_calls(self):
        d = discover(self.tmp.name)
        self.assertEqual(d["layout"], "per_sample_mtx")
        self.assertEqual(sorted(s["name"] for s in d["samples"]), ["S1", "S2"])
        for s in d["samples"]:  # paired although the GEO sample IDs differ
            self.assertIn(s["name"], os.path.basename(s["calls"]))

    def test_feature_classes_by_declared_type_and_count_pattern(self):
        self.assertEqual(self.facts["data.features.Custom"]["value"]["role"], "hashtag-like")
        # every feature of the group is listed, with the cells each one tops: none is dropped for being rare
        self.assertEqual(sorted(self.result["library"]["hashtags"]), ["HT-1", "HT-2", "HT-3"])
        self.assertEqual(set(self.facts["data.features.Custom"]["value"]["cells_each_tops"]), {"HT-1", "HT-2", "HT-3"})
        self.assertTrue(self.facts["data.features.Antibody Capture"]["value"]["role"].startswith("co-expressed"))
        self.assertTrue(any(q["key"] == "data.hashtags" for q in self.result["questions"]))

    def test_library_from_the_feature_reference(self):
        self.assertEqual(self.facts["perturbation.targets"]["value"], 4)
        self.assertEqual(self.facts["perturbation.targets"]["status"], "reported")
        self.assertEqual(self.facts["perturbation.guides_per_target"]["value"], 2)
        self.assertEqual(self.facts["perturbation.controls"]["value"]["n_guides"], 6)
        # the reference's 'Non-Targeting' label, reported as it is; the user still confirms the controls
        self.assertEqual(self.facts["perturbation.controls"]["status"], "reported")

    def test_moi_units_donors_and_modality(self):
        moi = self.facts["perturbation.moi"]["value"]
        self.assertNotIn("class", moi)  # the shares, without a verdict word
        self.assertLess(moi["two_or_more_targets"], 0.3)
        self.assertEqual(self.facts["units.candidates"]["value"]["n"], 2)
        self.assertIn("units.pooled_donors", self.facts)
        ot = self.facts["perturbation.on_target"]["value"]
        self.assertNotIn("suggests", ot)  # the numbers and each target's fold change; the user names the modality
        self.assertLess(ot["median_log2fc"], -1)
        self.assertEqual(len(ot["log2fc_by_target"]), ot["targets_measured"])
        self.assertEqual(self.facts["perturbation.on_target"]["value"]["cells"], "cells with one called guide")
        self.assertEqual(self.facts["data.calls_naming"]["value"], "names")


    def test_design_written_as_yaml(self):
        import yaml
        path = os.path.join(self.tmp.name, "design.yaml")
        write_design(self.result, path)
        d = yaml.safe_load(open(path))
        self.assertEqual(d["schema"], "pertura.design/v1")
        self.assertEqual(d["perturbation"]["targets"]["value"], 4)
        self.assertTrue(len(d["unresolved"]) >= 4)


class TestDetectors(unittest.TestCase):
    def test_guide_names(self):
        facts, _ = dt.guide_names(["rs1|rs2_1", "SYVN1", "BIN1_1"], ["SYVN1", "ACTB"],
                                  pd.DataFrame({"feature_call": ["id1|id3"]}), ["id1", "id2", "id3"])
        f = {x["key"]: x["value"] for x in facts}
        self.assertEqual(f["data.guide_names.separator"]["n"], 1)
        self.assertEqual(f["data.guide_names.gene_named"], ["SYVN1"])
        self.assertEqual(f["data.calls_naming"], "feature_ids")

    def test_without_labels_the_code_makes_no_map(self):
        # no naming rule: guide names are reported as they are, and the agent is asked for the target map
        facts, qs, target_of = dt.library(["BACH2_1", "BACH2_2", "NTC_1", "safe_3"], None)
        keys = {f["key"] for f in facts}
        self.assertEqual(target_of, {})
        self.assertEqual(keys, {"perturbation.guides"})
        self.assertTrue(any(q["key"] == "perturbation.target_map" for q in qs))
        moi, _ = dt.moi({"a": [("BACH2_1", 5)], "b": [("BACH2_1", 5), ("BACH2_2", 4)]}, target_of, 0)
        self.assertEqual(moi[0]["value"]["basis"], "guides (no target map yet)")
        mod, _ = dt.modality(None, target_of, ["BACH2"])
        self.assertIn("needs the target map", {f["key"]: f for f in mod}["perturbation.on_target"]["value"])

    def test_reference_labels_reported_as_they_are(self):
        # labels that disagree with the guide names are kept: the agent reads the names; the code does not
        ref = pd.DataFrame({"name": ["C14_1", "C14_2", "NTC_1", "AAVS1_1"],
                            "target_gene_name": ["CD55", "CD55", "Non-Targeting", "AAVS1"]})
        facts, qs, target_of = dt.library(["C14_1", "C14_2", "NTC_1", "AAVS1_1"], ref)
        self.assertEqual(target_of, {"C14_1": "CD55", "C14_2": "CD55", "NTC_1": "control", "AAVS1_1": "AAVS1"})
        f = {x["key"]: x for x in facts}
        self.assertEqual(f["perturbation.controls"]["value"]["n_guides"], 1)  # only Cell Ranger's label
        self.assertEqual(f["perturbation.targets"]["value"], 2)
        self.assertTrue(any(q["key"] == "perturbation.controls" for q in qs))  # is a labelled target a control?

    def test_object_columns_flag_values_that_are_not_guides(self):
        obs = pd.DataFrame({"sgRNA": ["A_g1", "WT", "A_g1,B_g2", "WT", "NT_g1"], "individual": list("ppqqp")})
        facts, qs = dt.object_columns(obs, ["A_g1", "B_g2", "NT_g1"])
        f = {x["key"]: x["value"] for x in facts}
        self.assertEqual(f["data.object.guide_column.sgRNA"]["values_not_guides"], {"WT": 2})
        self.assertTrue(any("WT" in q["question"] for q in qs))

    def test_parse_calls_without_umis_or_with_numbers(self):
        # cells_per_protospacer.json gives no UMIs; under pandas 3, astype(str) keeps them missing (NaN), so the
        # parser must take missing and numeric values as they come (GSE267740 failed on this)
        t = pd.DataFrame({"cell_barcode": ["a", "b", "c", "d"], "feature_call": ["g1", "g1|g2", "g2", np.nan],
                          "num_features": [1, 2, 1, 1], "num_umis": [np.nan, None, np.int64(7), 3.0]},
                         dtype=object)
        calls = dt.parse_calls(t, ["g1", "g2"])
        self.assertEqual(calls, {"a": [("g1", 1)], "b": [("g1", 1), ("g2", 1)], "c": [("g2", 7)]})

    def test_every_matrix_contributes_its_features(self):
        # each sample carries its own hashtags (GSE263761: one set per condition); reading only the first sample's
        # features left the other samples' cells without a hashtag
        with tempfile.TemporaryDirectory() as d:
            make_deposit(d, hashtags={"S1": ["HT-1", "HT-2", "HT-3"], "S2": ["HT-4", "HT-5", "HT-6"]})
            r = audit(d)
        facts = {f["key"]: f["value"] for f in r["facts"]}
        self.assertEqual(facts["data.features.Custom"]["n"], 6)
        self.assertEqual(len(r["library"]["hashtags"]), 6)  # every sample's hashtags
        by = facts["units.hashtag_calls_by_method"]
        if isinstance(by, str):  # without R and Seurat the measurement says so
            self.assertTrue(by.startswith("not measured"))
            return
        self.assertEqual(set(by) >= {"htodemux", "multiseqdemux"}, True)
        tagged = sum(facts["units.by_hashtag.candidates"]["cells"].values())
        self.assertGreater(tagged, 0.9 * 800)  # cells of both samples carry a hashtag

    def test_split_calls(self):
        self.assertEqual(split_calls("rs1|rs2_1|BIN1_1", 2, {"rs1|rs2_1", "BIN1_1"}), ["rs1|rs2_1", "BIN1_1"])

    def test_weak_guide_capture_and_readouts(self):
        G = pd.DataFrame({"a": [700, 650, 40, 30], "b": [5, 9, 30, 35]})
        facts, qs = dt.guide_capture_by_unit(G, ["L1", "L1", "L2", "L2"])
        weak = {f["key"]: f["value"] for f in facts}.get("units.weak_guide_capture", {})
        self.assertEqual(list(weak), ["L2"])  # 10x fewer guide UMIs than the other unit
        G3 = pd.DataFrame({"a": [700, 650, 720, 690, 40, 30], "b": [5, 9, 4, 6, 30, 35]})
        facts, qs = dt.guide_capture_by_unit(G3, ["L1", "L1", "L3", "L3", "L2", "L2"])
        weak = {f["key"]: f["value"] for f in facts}.get("units.weak_guide_capture", {})
        self.assertEqual(list(weak), ["L2"])
        facts, qs = dt.readouts({"6h": 15000, "12h": 14500, "6h_targeted": 400})
        self.assertIn("6h_targeted", facts[0]["value"]["targeted_looking"])

    def test_one_label_for_most_guides_is_not_used_as_a_map(self):
        # GSE319266 labels all 1306 guides 'CD55': reported as written, but the MOI is measured by guides, and the
        # agent is asked for the map
        guides = [f"SNP-{i}-{k}" for i in range(1, 11) for k in (1, 2, 3)]
        ref = pd.DataFrame({"name": guides, "target_gene_name": "CD55"})
        facts, qs, target_of = dt.library(guides, ref)
        f = {x["key"]: x["value"] for x in facts}
        self.assertEqual(target_of, {})
        self.assertEqual(f["perturbation.reference_labels"]["largest"], {"CD55": 30})
        self.assertEqual(f["perturbation.targets"], 1)  # what the reference says, as it says it
        self.assertTrue(any(q["key"] == "perturbation.target_map" and "CD55" in q["question"] for q in qs))
        two = pd.DataFrame({"name": guides, "target_gene_name": ["CD55"] * 15 + ["CD46"] * 15})
        self.assertEqual(len(dt.library(guides, two)[2]), 30)  # two genes, half each: a map

    def test_top_guide_share_flags_a_lane_with_ambient_guides(self):
        # L2 captures as many guide UMIs as the others, but spread over many guides: a small top-guide share
        rng = np.random.default_rng(1)
        n, k = 40, 30
        rows, units = [], []
        for u in ("L1", "L2", "L3"):
            for _ in range(n):
                r = rng.poisson(3, k).astype(float)
                if u != "L2":
                    r[rng.integers(k)] += 300
                else:
                    r += rng.poisson(8, k)
                rows.append(r)
                units.append(u)
        G = pd.DataFrame(rows, columns=[f"g{i}" for i in range(k)])
        facts, qs = dt.guide_capture_by_unit(G, units)
        f = {x["key"]: x["value"] for x in facts}
        self.assertEqual(list(f["units.weak_guide_capture"]), ["L2"])
        self.assertLess(f["units.guide_capture"]["L2"]["top_share"], 0.2)
        self.assertGreater(f["units.guide_capture"]["L1"]["top_share"], 0.7)
        self.assertTrue(any(q["key"] == "units.weak_guide_capture" for q in qs))

    def test_units_reach(self):
        unit = ["u1"] * 30 + ["u2"] * 30 + ["u3"] * 30
        pert = (["control"] * 15 + ["T"] * 15) * 3
        facts, _, tested = dt.units(unit, pert, "samples")
        self.assertEqual(tested, 1.0)
        facts, _, tested = dt.units(unit[:60], pert[:60], "samples")  # two units: pseudobulk cannot test
        self.assertEqual(tested, 0.0)


def write_triplet(prefix, X, feats, barcodes):
    """A 10x triplet: X is cells x features; feats rows are (id, name) or (id, name, type)."""
    with gzip.open(prefix + "matrix.mtx.gz", "wb") as fh:
        scipy.io.mmwrite(fh, sp.csr_matrix(np.asarray(X, float).T))
    pd.DataFrame(feats).to_csv(prefix + "features.tsv.gz", sep="\t", header=False, index=False)
    pd.Series(barcodes).to_csv(prefix + "barcodes.tsv.gz", header=False, index=False)


class TestDepositFormats(unittest.TestCase):
    def test_line_endings_of_a_feature_reference(self):
        rows = ["id,name,read,pattern,sequence,feature_type,target_gene_id,target_gene_name",
                "g1,TA_1,R2,(BC),ACGT,CRISPR Guide Capture,,TA", "g2,NTC_1,R2,(BC),ACGT,CRISPR Guide Capture,,"
                "Non-Targeting"]
        with tempfile.TemporaryDirectory() as d:
            for name, end in (("cr.csv", "\r"), ("crlf.csv", "\r\n"), ("lf.csv", "\n")):
                with open(os.path.join(d, name), "w", newline="") as fh:
                    fh.write(end.join(rows) + end)
                t = read_feature_reference(os.path.join(d, name))
                self.assertEqual(list(t["name"]), ["TA_1", "NTC_1"], name)
                self.assertEqual(list(t["target_gene_name"]), ["TA", "Non-Targeting"], name)

    def test_a_separate_guide_matrix_is_paired_by_barcodes(self):
        # expression and guides deposited as two triplets of the same cells; the guide features file has no type
        # column, and the feature reference declares them guides (GSE220974's layout)
        rng = np.random.default_rng(2)
        n, genes, guides = 300, ["TA", "TB"] + [f"G{i}" for i in range(40)], ["TA_1", "TB_1", "NTC_1", "NTC_2"]
        bcs = [f"AC{i:05d}-1" for i in range(n)]
        guide = rng.choice(guides, n)
        X = rng.poisson(5, (n, len(genes))).astype(float)
        X[:, :2] = rng.poisson(20, (n, 2))
        for i, g in enumerate(guide):
            if g.startswith("T"):
                X[i, ["TA_1", "TB_1"].index(g)] = 0
        Gm = np.zeros((n, len(guides)))
        Gm[np.arange(n), [guides.index(g) for g in guide]] = rng.integers(30, 90, n)
        with tempfile.TemporaryDirectory() as d:
            write_triplet(os.path.join(d, "GSM1_cells_gex_"), X, [(g, g, "Gene Expression") for g in genes], bcs)
            order = rng.permutation(n)  # the same cells, in another order
            write_triplet(os.path.join(d, "GSM1_cells_grna_"), Gm[order], [(f"id_{g}", g) for g in guides],
                          [bcs[i] for i in order])
            pd.DataFrame({"id": [f"id_{g}" for g in guides], "name": guides, "read": "R2", "pattern": "(BC)",
                          "sequence": "ACGT", "feature_type": "CRISPR Guide Capture",
                          "target_gene_name": ["TA", "TB", "Non-Targeting", "Non-Targeting"]}) \
                .to_csv(os.path.join(d, "feature_reference.csv"), index=False)
            dep = discover(d)
            self.assertEqual(len(dep["samples"]), 1)
            s = dep["samples"][0]
            self.assertEqual(len(s["extra"]), 1)
            self.assertTrue(s["features"].endswith("gex_features.tsv.gz"))
            a = read_matrix(s, {"id_" + g: "CRISPR Guide Capture" for g in guides})
            self.assertEqual(int((a.var["feature_types"] == "CRISPR Guide Capture").sum()), 4)
            j = list(a.var["name"]).index("NTC_2")
            want = Gm[:, guides.index("NTC_2")]
            self.assertTrue(np.array_equal(np.asarray(a.X[:, j].todense()).ravel(), want))  # rows realigned
            r = audit(d)
        f = {x["key"]: x["value"] for x in r["facts"]}
        self.assertEqual(f["perturbation.guides"]["n"], 4)
        if f["perturbation.assignment_source"].startswith("not measured"):  # scikit-learn's mixture fails here
            return
        self.assertLess(f["perturbation.on_target"]["median_log2fc"], -1)
        self.assertTrue(f["perturbation.assignment_source"].startswith("Cell Ranger's own method"))

    def test_two_samples_of_one_folder_name_are_named_by_their_folders(self):
        """A deposit's libraries: Part_A/Library1 and Part_B/Library1 were both 'Library1', and the second cell
        table written over the first."""
        rng = np.random.default_rng(4)
        feats = [(f"G{i}", f"G{i}", "Gene Expression") for i in range(20)]
        with tempfile.TemporaryDirectory() as d:
            for part, start in (("Part_A", 0), ("Part_B", 50)):
                os.makedirs(os.path.join(d, part, "Library1"))
                write_triplet(os.path.join(d, part, "Library1", ""), rng.poisson(5, (50, 20)), feats,
                              [f"AC{i:05d}-1" for i in range(start, start + 50)])
            os.makedirs(os.path.join(d, "Part_A", "Library2"))
            write_triplet(os.path.join(d, "Part_A", "Library2", ""), rng.poisson(5, (50, 20)), feats,
                          [f"AC{i:05d}-1" for i in range(100, 150)])
            names = sorted(s["name"] for s in discover(d)["samples"])
        self.assertEqual(names, ["Library2", "Part_A_Library1", "Part_B_Library1"])

    def test_a_matrix_of_every_droplet_is_not_read_as_cells_and_says_what_to_give(self):
        """A deposit's raw libraries (millions of barcodes each, no filtered matrix) took the measurement past 350 GB; a
        combined matrix of many lanes, each of called cells, is read."""
        from unittest import mock
        from pertura.design_audit import readers
        feats = [(f"G{i}", f"G{i}", "Gene Expression") for i in range(20)]
        rng = np.random.default_rng(5)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(readers, "EVERY_DROPLET", 100):
            os.makedirs(os.path.join(d, "raw"))
            write_triplet(os.path.join(d, "raw", ""), rng.poisson(1, (150, 20)), feats,
                          [f"AC{i:05d}-1" for i in range(150)])
            os.makedirs(os.path.join(d, "lanes"))
            write_triplet(os.path.join(d, "lanes", ""), rng.poisson(5, (160, 20)), feats,
                          [f"GT{i:05d}-{1 + i // 80}" for i in range(160)])
            dep = discover(d)
            self.assertEqual([s["name"] for s in dep["samples"]], ["lanes"])
            self.assertEqual([e["barcodes_in_one_lane"] for e in dep["every_droplet"]], [150])
            r = audit(d)
        f = {x["key"]: x["value"] for x in r["facts"]}
        self.assertEqual(f["data.every_droplet"][0]["barcodes_in_one_lane"], 150)

    def test_call_files_of_one_combined_matrix_paired_by_barcodes(self):
        # one matrix of two lanes ('-1', '-2'); each lane's call file names its cells '-1'
        rng = np.random.default_rng(3)
        n, genes, guides = 200, ["TA"] + [f"G{i}" for i in range(30)], ["TA_1", "NTC_1"]
        stems = [f"AC{i:05d}" for i in range(n)]
        bcs = [f"{x}-1" for x in stems[:100]] + [f"{x}-2" for x in stems[100:]]
        guide = rng.choice(guides, n)
        X = np.hstack([rng.poisson(5, (n, len(genes))), np.zeros((n, 2))])
        X[np.arange(n), len(genes) + np.array([guides.index(g) for g in guide])] = 50
        feats = [(g, g, "Gene Expression") for g in genes] + [(f"id_{g}", g, "CRISPR Guide Capture") for g in guides]
        with tempfile.TemporaryDirectory() as d:
            write_triplet(os.path.join(d, "GSE9_combined_"), X, feats, bcs)
            for lane, rows in (("A", range(100)), ("B", range(100, 200))):
                pd.DataFrame({"cell_barcode": [f"{stems[i]}-1" for i in rows], "num_features": 1,
                              "feature_call": [guide[i] for i in rows], "num_umis": 50}) \
                    .to_csv(os.path.join(d, f"lane{lane}_calls.csv"), index=False)
            dep = discover(d)
            s = dep["samples"][0]
            self.assertEqual(s["calls_pairing"], "by barcodes")
            self.assertEqual({os.path.basename(c["path"]): c["suffix"] for c in s["calls"]},
                             {"laneA_calls.csv": None, "laneB_calls.csv": "2"})
            r = audit(d)
        f = {x["key"]: x["value"] for x in r["facts"]}
        self.assertEqual(f["perturbation.moi"]["cells_with_calls"], 200)  # both lanes' cells are assigned
        self.assertEqual(f["units.candidates"]["n"], 2)
        # named as the build names them, so a proposal's keys are the screen's units (GSE190604's dry run)
        self.assertEqual(set(f["units.candidates"]["cells"]), {f"{s['name']}-1", f"{s['name']}-2"})

    def test_the_form_names_the_units_the_build_will_count_and_a_confirmed_number_must_match(self):
        # development run on GSE203592, 2026-11-05: the user said mouse 2's two captures are one, the design's source
        # said "merged", and the build counted both
        from pertura.design_audit import checks
        lib = {"samples": ["M1", "M2A", "M2B", "M3"]}

        def check(n=None, merge=None):
            units = {"independent_unit": {"value": "mouse", "status": "confirmed", "source": "t"}}
            if n is not None:
                units["n_units"] = {"value": n, "status": "confirmed", "source": "t"}
            if merge:
                units["merge"] = {"value": merge, "status": "confirmed", "source": "t"}
            problems, flags = [], []
            checks._units({"units": units}, {}, lib, problems, flags)
            return problems, flags
        problems, flags = check()
        self.assertIn("the build will count 4 mice: M1, M2A, M2B, M3, one per sample", [f["text"] for f in flags])
        self.assertEqual([p for p in problems if "n_units" in p], [])
        problems, _ = check(3)
        self.assertTrue(any(p.startswith("units.n_units: 3 mice, but the units as proposed make 4") for p in problems),
                        problems)
        self.assertEqual([p for p in check(4)[0] if "n_units" in p], [])
        # GSE203592 on 99eab77: the form said 8 with the two captures merged, and the build's modules counted 7
        problems, flags = check(3, merge={"M2B": "M2A"})
        self.assertIn("the build will count 3 mice: M1, M2A, M3, one per sample, after units.merge",
                      [f["text"] for f in flags])
        self.assertEqual([p for p in problems if "n_units" in p], [])

    def test_the_unit_is_one_word_so_code_never_reads_a_sentence(self):
        # 2026-11-15: the unit was read from the first word of a sentence, so "two donors, not identifiable" and
        # "lanes of one pool" were replicates and "cells from 4 donors" none. Now a sentence is a problem before the
        # form, and the one word is looked up.
        from pertura import design_gate
        from pertura.design_audit import checks
        lib = {"samples": ["L1", "L2"]}

        def problems_of(unit):
            problems, flags = [], []
            units = {"independent_unit": {"value": unit, "status": "proposed", "source": "t"}}
            checks._units({"units": units}, {}, lib, problems, flags)
            return [p for p in problems if p.startswith("units.independent_unit")]
        for unit in ("donor", "mouse", "culture", "none", "cell"):
            self.assertEqual(problems_of(unit), [], unit)
        for unit in ("two donors, not identifiable", "lanes of one pool", "cells from 4 donors", "N/A",
                     "cell (no biological replicate: two captures of one pool)"):
            self.assertTrue(problems_of(unit) and "is not one word" in problems_of(unit)[0], unit)
        for unit in (None, "", "none", "None", " cells ", "cell"):
            self.assertTrue(design_gate.no_replicate(unit), unit)
        for unit in ("donor", "mouse", "culture", "two donors, not identifiable", "lanes of one pool", "N/A",
                     {"source": "hashtag"}):
            self.assertFalse(design_gate.no_replicate(unit), unit)

    def test_a_unit_confirmed_as_a_sentence_before_is_asked_again_not_read(self):
        # d2's review of bc95609: a design confirmed with "cell (no biological replicate: ...)" would now open as
        # replicates without a word; the gate lists it as not confirmed instead, so the form asks it again
        from pertura import design_gate
        key = "units.independent_unit"

        def confirmed(unit):
            return {"units": {"independent_unit": {"value": unit, "status": "confirmed", "source": "t"}},
                    "confirmations": [{"key": key, "value": {"independent_unit": unit, "unit_column": None}}]}
        old = design_gate.missing_keys(confirmed("cell (no biological replicate: five captures of one pool)"), [key])
        self.assertIn("is not one word", old.get(key, ""), old)
        for unit in ("donor", "none", "cell"):
            self.assertEqual(design_gate.missing_keys(confirmed(unit), [key]), {}, unit)

    def test_cellranger_configs_and_sample_sheets(self):
        multi = ("[gene-expression]\r\nreference,/refs/GRCh38\r\n\r\n[feature]\r\nreference,feature_ref.csv\r\n"
                 "\r\n[libraries]\r\nfastq_id,fastqs,feature_types\r\nGEX1,/fq,Gene Expression\r\n"
                 "CRISPR1,/fq,CRISPR Guide Capture\r\n\r\n[samples]\r\nsample_id,cmo_ids,description\r\n"
                 "mouse1,CMO301,day 7\r\nmouse2,CMO302,day 7\r\n")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "run_multi_config.csv"), "w", newline="") as fh:
                fh.write(multi)
            pd.DataFrame({"fastqs": ["/fq"], "sample": ["S1"], "library_type": ["Gene Expression"]}) \
                .to_csv(os.path.join(d, "libraries.csv"), index=False)
            pd.DataFrame({"sample": ["S1", "S2"], "treatment": ["IFNg", "none"]}) \
                .to_csv(os.path.join(d, "sample_sheet.tsv"), sep="\t", index=False)
            dep = discover(d)
            self.assertEqual(sorted(os.path.basename(c) for c in dep["configs"]), ["libraries.csv",
                                                                                   "run_multi_config.csv"])
            self.assertEqual([os.path.basename(t) for t in dep["tables"]], ["sample_sheet.tsv"])
            cfg = read_cellranger_config(os.path.join(d, "run_multi_config.csv"))
            lib = read_cellranger_config(os.path.join(d, "libraries.csv"))
        self.assertEqual(cfg["gene-expression"], [{"key": "reference", "value": "/refs/GRCh38"}])
        self.assertEqual([x["sample_id"] for x in cfg["samples"]], ["mouse1", "mouse2"])
        self.assertEqual([x["feature_types"] for x in cfg["libraries"]], ["Gene Expression", "CRISPR Guide Capture"])
        self.assertEqual(lib["libraries"][0]["library_type"], "Gene Expression")


class TestFasterMeasurementSameResults(unittest.TestCase):
    """The measurement's faster steps give exactly what the plain formulas give."""

    def setUp(self):
        rng = np.random.default_rng(3)
        X = sp.random(900, 300, density=0.2, random_state=4, format="csr", dtype=np.float64)
        X.data = np.ceil(X.data * 9)
        X.data[::50] = 0  # explicit zeros, as some writers store
        self.X = sp.csr_matrix(X.astype(np.int64))
        rows = np.arange(900)
        self.A = sp.csr_matrix((np.ones(600, np.float32), (rng.choice(rows, 600, replace=False),
                                                            rng.integers(0, 12, 600))), shape=(900, 12))

    def test_the_per_guide_summary(self):
        from pertura.design_audit import audit as au
        X, A = self.X, self.A
        out = au.guide_expression(X, [f"g{j}" for j in range(300)], A, [f"G{k}" for k in range(12)], chunk=250)
        det = np.asarray((X > 0).sum(0)).ravel()
        keep = np.flatnonzero(det >= au.SUMMARY_MIN_DETECTION * X.shape[0])
        S, D = np.zeros((12, len(keep))), np.zeros((12, len(keep)), np.int64)
        for r in range(0, 900, 250):  # the formula as first written
            Xc = X[r:r + 250][:, keep]
            lib = np.asarray(X[r:r + 250].sum(1)).ravel()
            Ac = sp.csr_matrix(A[r:r + 250]).T.tocsr()
            S += np.asarray((Ac @ (sp.diags(1e4 / np.maximum(lib, 1)) @ Xc)).todense())
            D += np.asarray((Ac @ (Xc > 0).astype(np.float32)).todense()).astype(np.int64)
        self.assertTrue(np.array_equal(out["sum"], S.astype(np.float32)))
        self.assertTrue(np.array_equal(out["detected"], D.astype(np.int32)))

    def test_counts_over_rows_in_blocks_and_scattered(self):
        from pertura.design_audit import audit as au
        X = self.X
        for r in (np.arange(100, 400), np.array([3, 7, 8, 500, 899]), np.array([], dtype=int)):
            self.assertTrue(np.array_equal(au._detected_in_rows(X, r), np.asarray((X[r] > 0).sum(0)).ravel()))
            if len(r):
                self.assertTrue(np.array_equal(au._rows(X, r).toarray(), X[r].toarray()))


class TestTheCellsOfAMatrixOfEveryDroplet(unittest.TestCase):
    def test_the_deposits_calls_else_cell_rangers_call_in_each_sample(self):
        import numpy as np
        from pertura.design_audit.audit import likely_cells
        rng = np.random.default_rng(0)
        cells_a, empty_a = rng.poisson(4000, 2500), rng.poisson(8, 60000)  # one sample: 2,500 cells among empties
        cells_b, empty_b = rng.poisson(900, 1500), rng.poisson(5, 40000)   # another, shallower: its own line
        umis = np.concatenate([cells_a, empty_a, cells_b, empty_b])
        sample = np.array(["a"] * (2500 + 60000) + ["b"] * (1500 + 40000))
        mask, words = likely_cells(umis, sample)
        truth = np.zeros(len(umis), bool)
        truth[:2500] = True
        truth[62500:64000] = True
        self.assertTrue((mask == truth).all())  # each sample read against its own line
        self.assertIn("Cell Ranger's cell call", words)
        listed = np.zeros(len(umis), bool)
        listed[:10] = True
        mask, words = likely_cells(umis, sample, listed)
        self.assertEqual((int(mask.sum()), words), (10, "the cells the deposit's call files list"))


if __name__ == "__main__":
    unittest.main()


def test_the_ambient_guide_share_stands_beside_the_calling_rule():
    # docs/adr/0037, the D4 follow-up: in one deposit's library about 80% of the guide UMIs lay outside the cells
    from pertura.design_audit.audit import ambient_guide_share
    from pertura.design_service import measured_flags
    umis = np.array([10, 0, 5, 5, 80])
    cells = np.array([True, False, False, False, True])
    sample = np.array(["a", "a", "a", "b", "b"])
    f = ambient_guide_share(umis, cells, sample, raw=True)
    assert f["value"] == {"outside_cells": 0.1, "by_sample": {"a": 0.333, "b": 0.059}}
    words = measured_flags({f["key"]: f}, {"perturbation.assignment"})["perturbation.assignment"]
    assert words == ["10% of the guide UMIs lie in barcodes that are not cells: a guide called on one or two UMIs "
                     "may be ambient"]
    filtered = ambient_guide_share(umis, cells, sample, raw=False)
    assert "not measured" in filtered["value"]
    assert measured_flags({filtered["key"]: filtered}, {"perturbation.assignment"}) == {}
