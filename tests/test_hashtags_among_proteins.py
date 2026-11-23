"""Hashtags among surface proteins (design_audit.detectors.hashtags_among): a few hashtags declared in one feature type
with a panel of antibodies, as in a CITE-seq screen whose hashtags and proteins are all 'Antibody Capture', are found
from the counts alone, and the measurement calls each cell's hashtag from them, per sample and per lane."""
import gzip
import os
import tempfile
import unittest

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

from pertura import envs
from pertura.design_audit import detectors as dt
from pertura.design_audit.audit import audit

HAS_R = os.path.exists(envs.rscript())
TAGS = ["Tag_A", "Tag_B", "Tag_C", "Tag_D"]


def panel(n=2000, proteins=40, tags=TAGS, shares=None, seed=0, negatives=0.08, doublets=0.08):
    """Cells x features: proteins co-expressed in every cell (one abundant everywhere, two marking exclusive halves of
    the cells at lower counts), and each cell's own hashtag high over a little background; some cells carry two
    hashtags, some none. shares: each hashtag's share of the tagged cells (default even)."""
    rng = np.random.default_rng(seed)
    P = rng.poisson(rng.gamma(2, 10, proteins), size=(n, proteins)).astype(float)
    P[:, 0] = rng.poisson(150, n)
    half = rng.random(n) < 0.5
    P[:, 1] = np.where(half, rng.poisson(120, n), rng.poisson(2, n))
    P[:, 2] = np.where(half, rng.poisson(2, n), rng.poisson(120, n))
    names = [f"CD{i}" for i in range(proteins)]
    if not tags:
        return pd.DataFrame(P, columns=names), None
    H = rng.poisson(3, size=(n, len(tags))).astype(float)
    own = rng.choice(len(tags), size=n, p=shares)
    H[np.arange(n), own] += rng.poisson(400, n)
    k, m = int(doublets * n), int(negatives * n)
    H[:k, (own[:k] + 1) % len(tags)] += rng.poisson(400, k)
    H[n - m:] = rng.poisson(2, size=(m, len(tags)))
    return pd.DataFrame(np.hstack([P, H]), columns=names + list(tags)), own


def classes(counts):
    var = pd.DataFrame({"name": counts.columns, "feature_types": "Antibody Capture"})
    facts, qs, hashtags = dt.feature_classes(var, counts)
    return {f["key"]: f for f in facts}, qs, hashtags


class TestHashtagsAmongProteins(unittest.TestCase):
    def test_a_few_hashtags_among_proteins_are_found_by_their_counts(self):
        counts, _ = panel()
        facts, qs, hashtags = classes(counts)
        self.assertEqual(hashtags, TAGS)
        value = facts["data.features.Antibody Capture"]["value"]
        self.assertTrue(value["role"].startswith("co-expressed"))
        self.assertIn("hashtag-like", value["role"])
        self.assertEqual(value["hashtag_like"]["features"], TAGS)
        self.assertGreater(value["hashtag_like"]["they_top_share_of_cells"], 0.8)
        self.assertEqual(len(value["cells_each_tops"]), 44)  # every feature of the group is still listed
        keys = [q["key"] for q in qs]
        self.assertIn("data.hashtags", keys)
        ask = next(q["question"] for q in qs if q["key"] == "data.features.Antibody Capture")
        self.assertIn("4 of them behave like hashtags", ask)

    def test_the_names_play_no_part(self):
        counts, _ = panel()
        shuffled = counts.copy()
        shuffled.columns = [f"F{i}" for i in range(counts.shape[1])]
        _, _, hashtags = classes(shuffled)
        self.assertEqual(hashtags, [f"F{i}" for i in range(40, 44)])

    def test_a_small_hashtag_is_kept(self):
        counts, _ = panel(shares=[0.45, 0.3, 0.22, 0.03])
        _, _, hashtags = classes(counts)
        self.assertEqual(hashtags, TAGS)

    def test_a_protein_raised_in_some_hashtags_cells_is_not_a_hashtag(self):
        """A protein that activation raises (CD71 after restimulation) is high in the cells of the hashtags that mark
        activated cells, and low in the others: in a few cells it tops the panel, and there it holds many times its
        median elsewhere. It is not apart from those hashtags, so it is not one."""
        rng = np.random.default_rng(3)
        counts, own = panel(n=4000, shares=[0.3, 0.3, 0.2, 0.2], seed=3)
        weak = rng.random(len(counts)) < 0.12  # cells whose hashtag stained weakly
        for t in TAGS:
            counts.loc[weak, t] = np.round(counts.loc[weak, t] * 0.2)
        raised = np.isin(own, [2, 3])  # Tag_C and Tag_D mark the activated cells
        counts["CD71"] = np.where(raised, rng.poisson(150, len(counts)), rng.poisson(1, len(counts)))
        facts, _, hashtags = classes(counts)
        self.assertEqual(hashtags, TAGS)
        value = facts["data.features.Antibody Capture"]["value"]
        self.assertGreater(value["cells_each_tops"]["CD71"], 0.01 * len(counts))  # it tops cells of its own
        self.assertIn("CD71", value["hashtag_like"]["left_out"])

    def test_proteins_alone_give_no_hashtags(self):
        counts, _ = panel(tags=[])
        facts, qs, hashtags = classes(counts)
        self.assertEqual(hashtags, [])
        value = facts["data.features.Antibody Capture"]["value"]
        self.assertEqual(value["role"], "co-expressed (surface proteins?)")
        self.assertNotIn("hashtag_like", value)
        self.assertNotIn("data.hashtags", [q["key"] for q in qs])

    def test_a_group_of_hashtags_alone_is_read_as_a_whole(self):
        counts, _ = panel()
        tags = counts[TAGS]
        var = pd.DataFrame({"name": TAGS, "feature_types": "Custom"})
        facts, _, hashtags = dt.feature_classes(var, tags)
        self.assertEqual(hashtags, TAGS)
        self.assertEqual({f["key"]: f for f in facts}["data.features.Custom"]["value"]["role"], "hashtag-like")


def one_matrix_of_lanes(root, n=1200, lanes=4, seed=0):
    """One sample of four lanes (barcode suffixes -1 to -4) with a Cell Ranger call file and a feature reference:
    4 targets x 2 guides and 6 non-targeting guides, and 30 proteins with the 4 hashtags, all 'Antibody Capture'."""
    rng = np.random.default_rng(seed)
    targets = ["TA", "TB", "TC", "TD"]
    guides = [f"{t}_{k}" for t in targets for k in (1, 2)] + [f"NTC_{k}" for k in range(1, 7)]
    genes = targets + ["XIST", "RPS4Y1"] + [f"G{i}" for i in range(60)]
    adt, _ = panel(n=n, proteins=30, seed=seed)
    feats = ([(g, g, "Gene Expression") for g in genes] + [(f"id_{g}", g, "CRISPR Guide Capture") for g in guides]
             + [(a, a, "Antibody Capture") for a in adt.columns])
    guide = rng.choice(guides, size=n)
    X = np.zeros((n, len(feats)))
    X[:, :len(genes)] = rng.poisson(5, size=(n, len(genes)))
    X[:, :len(targets)] = rng.poisson(20, size=(n, len(targets)))
    G0 = len(genes)
    for i, g in enumerate(guide):
        X[i, G0 + guides.index(g)] = rng.integers(20, 200)
        if not g.startswith("NTC"):
            X[i, targets.index(g.split("_")[0])] = rng.poisson(1)
    X[:, G0 + len(guides):] = adt.to_numpy()
    bcs = [f"AAAC{i:06d}-{i % lanes + 1}" for i in range(n)]
    prefix = os.path.join(root, "GSM001_S1_")
    with gzip.open(prefix + "matrix.mtx.gz", "wb") as fh:
        scipy.io.mmwrite(fh, sp.csr_matrix(X.T))
    pd.DataFrame(feats).to_csv(prefix + "features.tsv.gz", sep="\t", header=False, index=False)
    pd.Series(bcs).to_csv(prefix + "barcodes.tsv.gz", header=False, index=False)
    pd.DataFrame({"cell_barcode": bcs, "num_features": 1, "feature_call": guide,
                  "num_umis": [int(X[i, G0 + guides.index(g)]) for i, g in enumerate(guide)]}) \
        .to_csv(os.path.join(root, "GSM001_S1_protospacer_calls_per_cell.csv.gz"), index=False)
    pd.DataFrame({"id": [f"id_{g}" for g in guides], "name": guides, "read": "R2", "pattern": "(BC)",
                  "sequence": "ACGT", "feature_type": "CRISPR Guide Capture", "target_gene_id": "",
                  "target_gene_name": ["Non-Targeting" if g.startswith("NTC") else g.split("_")[0] for g in guides]}) \
        .to_csv(os.path.join(root, "GSE0_feature_reference.csv.gz"), index=False)


class TestTheMeasurement(unittest.TestCase):
    def test_hashtags_among_proteins_are_called_per_sample_and_per_lane(self):
        with tempfile.TemporaryDirectory() as root:
            one_matrix_of_lanes(root)
            r = audit(root)
        facts = {f["key"]: f["value"] for f in r["facts"]}
        self.assertEqual(r["library"]["hashtags"], TAGS)
        self.assertEqual(facts["data.features.Antibody Capture"]["hashtag_like"]["features"], TAGS)
        by = facts["units.hashtag_calls_by_method"]
        if not HAS_R:  # without R and Seurat the measurement says so
            self.assertTrue(str(by).startswith("not measured"))
            return
        for method in ("htodemux", "multiseqdemux"):
            self.assertEqual(sorted(by[method]["by_lane"]), ["S1-1", "S1-2", "S1-3", "S1-4"], method)
            self.assertEqual(list(by[method]["by_sample"]), ["S1"], method)
        shares = facts["units.hashtag_shares_by_lane"]
        self.assertEqual(sorted(shares), ["S1-1", "S1-2", "S1-3", "S1-4"])
        for lane in shares.values():  # one pool split over lanes: each hashtag about a quarter of every lane
            self.assertEqual(sorted(lane), TAGS)
            self.assertTrue(all(0.15 < x < 0.35 for x in lane.values()), lane)


if __name__ == "__main__":
    unittest.main()
