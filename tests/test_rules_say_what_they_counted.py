"""The rules that decide what is read say what they counted (docs/adr/0041, step 6, from the inventory of
2026-11-06). A cut-off or a name rule may decide a flag, never what is in view:
- the mitochondrial and ribosomal shares say how many genes they count, and say it where none is counted (a matrix
  named by gene IDs gives every cell a share of 0, which reads as clean cells, and a filter on it removes nothing);
- one definition of those genes serves the measurement, the quality rule and the states module;
- an expression feature named as one of the library's guides stays a gene, and the reading checks name it (every
  module dropped it unsaid, and a target's own gene with it where a guide bore its name);
- each build gives every sample's barcodes and the two samples sharing the most, whether or not a check fires;
- a matrix not read as cells, as a guide readout, says the numbers that made it one."""
import json
import os
import tempfile
from unittest import mock

import numpy as np
import pandas as pd
import scipy.sparse as sp

from test_reading_checks import DESIGN, TABLE, cells

from pertura import cells_overview, quality, reading_checks
from pertura.design_audit import audit


def test_each_share_says_the_genes_it_counts_and_says_so_where_it_counts_none():
    counted = quality.genes_counted(["MT-CO1", "mt-Nd1", "RPL3", "RPS27A", "GAPDH"])
    assert counted["pct_mito"]["genes"] == 2 and counted["pct_ribo"]["genes"] == 2
    assert quality.none_counted(counted) is None
    by_id = quality.genes_counted(["ENSG00000198804", "ENSG00000111640"])
    assert "pct_mito counts no gene" in quality.none_counted(by_id)
    X = sp.csr_matrix(np.array([[2, 2, 6, 0, 0], [0, 0, 0, 5, 5]], dtype=float))
    m = quality.measures_of(X, ["MT-CO1", "mt-Nd1", "RPL3", "RPS27A", "GAPDH"])
    assert list(m["pct_mito"].round(2)) == [0.4, 0.0] and list(m["pct_ribo"].round(2)) == [0.6, 0.5]


def test_the_measurement_says_how_many_genes_each_share_counts():
    X = sp.csr_matrix(np.array([[2, 6, 2], [0, 5, 5]], dtype=float))
    fact = audit.quality_fact(X, ["MT-CO1", "RPL3", "GAPDH"], ["s1", "s1"])
    assert "genes named mt-: 1" in fact["note"] and "named rps or rpl: 1" in fact["note"]
    fact = audit.quality_fact(X, ["ENSG1", "ENSG2", "ENSG3"], ["s1", "s1"])
    assert "pct_mito counts no gene" in fact["note"]


def test_features_named_as_the_librarys_guides_are_named_and_kept():
    found = reading_checks.check(cells(["s1", "s2"], ["A", "control"]), DESIGN, TABLE, genes=["GAPDH", "A_1"])
    named = [c for c in found if c["check"] == "genes named as the library's guides"]
    assert named and named[0]["features"] == ["A_1"]
    found = reading_checks.check(cells(["s1", "s2"], ["A", "control"]), DESIGN, TABLE, genes=["GAPDH", "A"])
    assert "genes named as the library's guides" not in {c["check"] for c in found}


def test_each_build_gives_every_samples_barcodes_and_the_most_shared_pair():
    two = cells(["s1", "s2"], ["A", "control"], barcodes={"s1": "AAA", "s2": "AAA"})  # one capture read twice
    read = reading_checks.samples_read(two)
    assert read["by_sample"]["s1"] == {"barcodes": 2, "with_100_umis_or_more": 2}
    assert read["most_shared"] == {"samples": ["s1", "s2"], "shared_barcodes": 2, "share_of_the_smaller": 1.0}
    apart = reading_checks.samples_read(cells(["s1", "s2"], ["A", "control"]))
    assert apart["most_shared"]["share_of_the_smaller"] == 0.0
    assert reading_checks.samples_read(cells(["s1"], ["A"]))["most_shared"] is None


def test_a_guide_readout_says_the_numbers_that_made_it_one():
    whole = {"features": ([f"G{i}" for i in range(12000)], None)}
    guides = {"features": ([f"g{i}" for i in range(60)], ["CRISPR Guide Capture"] * 60)}
    small = {"features": ([f"g{i}" for i in range(500)], None)}
    with mock.patch("pertura.design_audit.readers.matrix_features", side_effect=lambda s: s["features"]):
        assert audit.guide_readouts([whole, guides, small]) == [guides, small]
        assert audit.why_guide_readout(guides, [whole, guides, small]) == "100% of its 60 features declared as guides"
        assert audit.why_guide_readout(small, [whole, guides, small]) == "500 features, beside a matrix of 12,000"
        assert audit.why_guide_readout(whole, [whole, guides, small]) is None
        assert audit.guide_readouts([guides]) == []  # where every matrix is one, none is set aside


def test_the_cells_overview_says_when_no_gene_is_counted():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, cells_overview.FOLDER)
        os.makedirs(out)
        pd.DataFrame(columns=cells_overview.SUMMARY_COLUMNS).to_csv(os.path.join(out, "cluster_summary.tsv"), sep="\t",
                                                                    index=False)
        pd.DataFrame({"metric": ["pct_mito"], "cut": [0.1]}).to_csv(os.path.join(out, "cuts.tsv"), sep="\t", index=False)
        with open(os.path.join(out, "deposit_object_ranges.json"), "w", encoding="utf-8") as fh:
            json.dump(None, fh)
        with open(os.path.join(out, "note.json"), "w", encoding="utf-8") as fh:
            json.dump({"note": None, "genes_counted": quality.genes_counted(["ENSG1", "ENSG2"])}, fh)
        reply = cells_overview.reply(tmp)
        assert reply["genes_each_share_counts"]["pct_mito"]["genes"] == 0
        assert "pct_mito counts no gene" in reply["no_gene_counted"]
