"""Tests for the cell table (pertura.cell_table, docs/adr/0023): its checks, and a deposit read into cell tables by
code of the agent's own measuring and building as the same deposit read by Pertura's readers."""
import gzip
import os

import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from pertura import cell_table as ct
from pertura import screen_object as so
from pertura.design_audit import readers
from pertura.design_audit.audit import _plain, audit

from test_design_audit import make_deposit


def features(n_genes=3, guides=("g1", "g2")):
    rows = [(f"ENSG{i}", f"G{i}", "Gene Expression") for i in range(n_genes)]
    rows += [(f"id_{g}", g, "CRISPR Guide Capture") for g in guides]
    return pd.DataFrame(rows, columns=["gene_ids", "name", "feature_types"])


def test_the_checks_name_each_fault():
    f = features()
    X = sp.csr_matrix(np.array([[1, 0, 2, 3, 0], [0, 4, 0, 0, 5]]))
    assert ct.check(X, f, ["A-1", "B-1"]) == []
    assert "whole numbers" in " ".join(ct.check(X.multiply(0.5), f, ["A-1", "B-1"]))
    assert "whole numbers" in " ".join(ct.check(-X, f, ["A-1", "B-1"]))
    assert "repeated" in " ".join(ct.check(X, f, ["A-1", "A-1"]))
    assert "no 'feature_types'" in " ".join(ct.check(X, f.drop(columns="feature_types"), ["A-1", "B-1"]))
    assert "barcodes and" in " ".join(ct.check(X, f, ["A-1"]))
    calls = pd.DataFrame({"cell_barcode": ["A-1", "C-1"], "num_features": [1, 1], "feature_call": ["g1", "g9"],
                          "num_umis": [3, 2]})
    text = " ".join(ct.check(X, f, ["A-1", "B-1"], calls))
    assert "not among the table's barcodes" in text and "neither guide features nor listed guides" in text
    assert "neither" not in " ".join(ct.check(X, f, ["A-1", "B-1"], calls, guides=["g9"]))


def test_a_table_with_problems_is_not_entered(tmp_path):
    f = features()
    bad = ct.write(str(tmp_path), "S1", sp.csr_matrix(np.full((2, 5), 0.5)), f, ["A-1", "B-1"])
    assert not bad["ok"] and ct.manifest(str(tmp_path)) == {"samples": {}}
    good = ct.write(str(tmp_path), "S1", sp.csr_matrix(np.ones((2, 5))), f, ["A-1", "B-1"], made_by="run:run0001-x")
    assert good["ok"] and ct.made_by_agent(str(tmp_path)) == {"S1": "run:run0001-x"}
    assert [s["kind"] for s in ct.samples(str(tmp_path))] == ["cell_table"]


def _as_cell_tables(deposit, folder):
    """What an agent's reader would write: each sample of a deposit Pertura reads, as a cell table."""
    d = readers.discover(deposit)
    ref = readers.read_feature_reference(d["feature_references"][0]) if d["feature_references"] else None
    for s in d["samples"]:
        a = readers.read_matrix(s, readers.declared_types(ref))
        var = a.var[["gene_ids", "name", "feature_types"]].reset_index(drop=True)
        calls = readers.read_calls(s["calls"]) if s.get("calls") else None
        r = ct.write(folder, s["name"], a.X, var, list(a.obs_names), calls=calls, made_by="run:run0001-test",
                     source_files=[os.path.basename(s["matrix"])])
        assert r["ok"], r


def test_a_deposit_read_into_cell_tables_measures_as_read_by_pertura(tmp_path):
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    tables = str(tmp_path / "design" / "cell_tables")
    _as_cell_tables(str(dep), tables)
    plain = _plain(audit(str(dep)))
    tabled = _plain(audit(str(dep), cell_tables=tables))
    facts = {f["key"]: f["value"] for f in plain["facts"]}
    tfacts = {f["key"]: f["value"] for f in tabled["facts"]}
    assert tfacts["data.layout"] == "cell_tables" and set(tfacts["data.read_by"]) == {"S1", "S2"}
    for key in ("data.cells", "perturbation.moi", "units.pooled_donors", "perturbation.guide_names"):
        if key in facts:
            assert tfacts.get(key) == facts[key], key
    assert tabled["library"]["guides"] == plain["library"]["guides"]
    assert sorted(tabled["library"]["samples"]) == sorted(plain["library"]["samples"])


def test_the_screen_object_builds_the_same_from_cell_tables(tmp_path):
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    tables = str(tmp_path / "cell_tables")
    _as_cell_tables(str(dep), tables)
    guides = sorted({g for g in readers.read_calls(readers.discover(str(dep))["samples"][0]["calls"])["feature_call"]
                     for g in str(g).split("|")})
    targets = sorted({g.split("_")[0] for g in guides if not g.startswith("NTC")})
    design = {"perturbation": {"target_map": {"value": {t: [g for g in guides if g.split("_")[0] == t]
                                                        for t in targets}, "status": "confirmed"},
                               "controls": {"value": [{"kind": "non_targeting",
                                                       "guides": [g for g in guides if g.startswith("NTC")]}],
                                            "status": "confirmed"},
                               "assignment": {"value": {"rule": "deposit"}, "status": "confirmed"}},
              "units": {"independent_unit": {"value": "mouse", "status": "confirmed"}}}
    a = so.build(str(dep), design, str(tmp_path / "plain"))
    b = so.build(str(dep), design, str(tmp_path / "tabled"), cell_tables=tables)
    assert a["labels"] == b["labels"] and b["cell_tables"] == os.path.abspath(tables)
    ca = pd.read_csv(tmp_path / "plain" / "cells.tsv.gz", sep="\t")
    cb = pd.read_csv(tmp_path / "tabled" / "cells.tsv.gz", sep="\t")
    pd.testing.assert_frame_equal(ca, cb, check_dtype=False)  # the table keeps counts as whole numbers
    xa, xb = so.load(str(tmp_path / "plain"), cache=False), so.load(str(tmp_path / "tabled"), cache=False)
    assert (xa.X != xb.X).nnz == 0 and list(xa.var_names) == list(xb.var_names)


def test_an_unreadable_matrix_is_listed_and_the_question_says_what_to_do(tmp_path):
    dep = tmp_path / "dep"
    dep.mkdir()
    (dep / "S1_matrix.mtx.gz").write_bytes(b"not gzip at all")  # a matrix the readers cannot open
    (dep / "S1_features.tsv.gz").write_bytes(gzip.compress(b"G1\tG1\tGene Expression\n"))
    (dep / "S1_barcodes.tsv.gz").write_bytes(b"\x00\x05\x16\x07 not gzip either")
    d = readers.discover(str(dep))
    assert d["samples"] == [] and [os.path.basename(u["file"]) for u in d["unreadable"]] == ["S1_matrix.mtx.gz"]
    r = _plain(audit(str(dep)))
    assert any(f["key"] == "data.unreadable" for f in r["facts"])
    assert "cell tables" in " ".join(q["question"] for q in r["questions"])


def test_the_form_says_which_samples_the_agents_code_read(tmp_path):
    from pertura.design_service import DesignService, make_design_config
    from test_design_service import answer, proposal
    dep, state = tmp_path / "dep", tmp_path / "state"
    dep.mkdir()
    make_deposit(str(dep))
    _as_cell_tables(str(dep), str(state / "cell_tables"))
    svc = DesignService(make_design_config(str(dep), str(state)))
    d = svc.call("describe_deposit", {})[1]
    assert d["layout"] == "cell_tables" and {s["read_by"] for s in d["samples"]} == {"run:run0001-test"}
    assert svc.call("measure", {})[0]
    r = svc.call("check_design", {"proposal": proposal()})[1]
    assert any(f["key"] == "data.read_by" and "S1 (run:run0001-test)" in f["text"] for f in r["flags"])
    elicit = answer()
    assert svc.call("confirm_design", {}, elicit=elicit)[0]
    assert "read by the agent's own code" in elicit.calls[0][0]


def _design(guides, **units_and_conditions):
    """A confirmed design for make_deposit's guides, with the units and conditions given."""
    targets = sorted({g.split("_")[0] for g in guides if not g.startswith("NTC")})
    d = {"perturbation": {"target_map": {"value": {t: [g for g in guides if g.split("_")[0] == t] for t in targets},
                                         "status": "confirmed"},
                          "controls": {"value": [{"kind": "non_targeting",
                                                  "guides": [g for g in guides if g.startswith("NTC")]}],
                                       "status": "confirmed"},
                          "assignment": {"value": {"rule": "deposit"}, "status": "confirmed"}},
         "units": {k: {"value": v, "status": "confirmed"} for k, v in units_and_conditions.items() if k != "conditions"}}
    if "conditions" in units_and_conditions:
        d["experiment"] = {"conditions": {"value": units_and_conditions["conditions"], "status": "confirmed"}}
    return d


def _guides(dep):
    calls = readers.read_calls(readers.discover(dep)["samples"][0]["calls"])
    return sorted({g for v in calls["feature_call"].astype(str) for g in v.split("|")})


def _tables(dep, folder):
    """The deposit read into cell tables as Pertura's readers propose it."""
    d = readers.discover(dep)
    ref = readers.read_feature_reference(d["feature_references"][0]) if d["feature_references"] else None
    ct.propose(d, folder, readers.declared_types(ref))
    return folder


def test_units_are_built_as_the_user_confirmed_them(tmp_path):
    from pertura import hashtags
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    guides = _guides(str(dep))
    tables = _tables(str(dep), str(tmp_path / "tables"))
    by_sample = so.build(str(dep), _design(guides, independent_unit="donor"), str(tmp_path / "a"), cell_tables=tables)
    assert sorted(by_sample["by_unit"]) == ["S1", "S2"]
    # each cell's hashtag written into the tables by a rule named with its numbers (no R here); the field's
    # demultiplexers are tested with R
    rule = {"method": "top_ratio", "min_counts": 5, "ratio": 3}
    tags = ["HT-1", "HT-2", "HT-3"]
    written = hashtags.write_into_cell_tables(tables, tags, hashtags.PER_SAMPLE, value=rule, made_by="run:r1")
    assert written["ok"] and set(written["calls"]) == {"S1", "S2"}, written
    tagged = so.build(str(dep), _design(guides, independent_unit="donor", unit_column="hashtag"), str(tmp_path / "b"),
                      cell_tables=tables)
    units = set(tagged["by_unit"])
    assert {f"S1:HT-{k}" for k in (1, 2, 3)} <= units and "S1" not in units
    # one hashtag, one donor in every sample: the units are the hashtags themselves
    assert hashtags.write_into_cell_tables(tables, tags, hashtags.ONE_UNIT, value=rule)["ok"]
    pooled = so.build(str(dep), _design(guides, independent_unit="donor", unit_column="hashtag"), str(tmp_path / "c"),
                      cell_tables=tables)
    assert {f"HT-{k}" for k in (1, 2, 3)} <= set(pooled["by_unit"])


@pytest.mark.skipif(not os.path.exists(__import__("pertura.envs", fromlist=["rscript"]).rscript()),
                    reason="needs R with Seurat")
def test_units_by_the_fields_demultiplexers(tmp_path):
    from pertura import hashtags
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    guides = _guides(str(dep))
    tables = _tables(str(dep), str(tmp_path / "tables"))
    tags = ["HT-1", "HT-2", "HT-3"]
    r = hashtags.write_into_cell_tables(tables, tags, hashtags.PER_SAMPLE, value={"method": "multiseqdemux"},
                                        workdir=str(tmp_path))
    assert r["ok"], r
    built = so.build(str(dep), _design(guides, independent_unit="donor", unit_column="hashtag"),
                     str(tmp_path / "multiseqdemux"), cell_tables=tables)
    assert {f"S1:HT-{k}" for k in (1, 2, 3)} <= set(built["by_unit"])
    # the synthetic hashtags have no background, where HTODemux cannot fit its negative counts: said, never a
    # sample's cells lost quietly
    bad = hashtags.write_into_cell_tables(tables, tags, hashtags.PER_SAMPLE, value={"method": "htodemux"},
                                          workdir=str(tmp_path))
    assert not bad["ok"] and any("could not call its hashtags" in x for x in bad["problems"]), bad


def test_units_and_conditions_from_a_cell_table_column(tmp_path):
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    tables = str(tmp_path / "cell_tables")
    d = readers.discover(str(dep))
    ref = readers.read_feature_reference(d["feature_references"][0])
    for s in d["samples"]:  # the agent's reader adds a donor call and a treatment per cell
        a = readers.read_matrix(s, readers.declared_types(ref))
        n = a.n_obs
        obs = pd.DataFrame({"donor": np.where(np.arange(n) % 2, f"{s['name']}-d1", f"{s['name']}-d2"),
                            "treatment": np.where(np.arange(n) < n // 2, "drug", "vehicle")},
                           index=list(a.obs_names))
        r = ct.write(tables, s["name"], a.X, a.var[["gene_ids", "name", "feature_types"]].reset_index(drop=True),
                     list(a.obs_names), calls=readers.read_calls(s["calls"]), obs=obs, made_by="run:run0002-test")
        assert r["ok"], r
    design = _design(_guides(str(dep)), independent_unit="donor", unit_column="donor",
                     conditions={"column": "treatment", "names": ["drug", "vehicle"]})
    out = so.build(str(dep), design, str(tmp_path / "screen"), cell_tables=tables)
    assert sorted(out["by_unit"]) == ["S1-d1", "S1-d2", "S2-d1", "S2-d2"]
    cells = pd.read_csv(tmp_path / "screen" / "cells.tsv.gz", sep="\t")
    assert set(cells["condition"]) == {"drug", "vehicle"}
    assert all(f["flag"] != "columns not found" for f in out["flags"])
    missing = _design(_guides(str(dep)), independent_unit="donor", unit_column="pool")
    with pytest.raises(ValueError, match="units.unit_column 'pool'"):  # never a quiet fallback to the samples
        so.build(str(dep), missing, str(tmp_path / "screen2"), cell_tables=tables)


def test_other_values_are_flags_with_a_description_and_problems_without(tmp_path):
    from pertura import design_gate
    from pertura.design_audit import checks
    lib = {"guides": ["a_1", "c_1"], "cells_per_guide": {"a_1": 5, "c_1": 5}, "samples": ["S1", "S2"],
           "cell_table_columns": {"donor": ["d1", "d2"], "treatment": ["drug", "vehicle", "none"]}}
    base = {"schema": "pertura.design/v1",
            "perturbation": {"target_map": {"value": {"A": ["a_1"]}, "status": "proposed", "source": "names"},
                             "controls": {"value": [{"name": "C", "kind": "other", "description": "scrambled guides",
                                                     "guides": ["c_1"]}], "status": "proposed", "source": "names"},
                             "modality": {"value": "CRISPRi", "status": "reported", "source": "paper"}},
            "units": {"independent_unit": {"value": "donor", "status": "proposed", "source": "the reader"},
                      "unit_column": {"value": "donor", "status": "proposed", "source": "demultiplexed by SNPs"}},
            "experiment": {"conditions": {"value": {"column": "treatment", "names": ["drug", "vehicle"]},
                                          "status": "proposed", "source": "cell tables"}},
            "unresolved": []}
    r = checks.check(base, {"facts": [], "library": lib})
    text = " ".join(r["problems"])
    assert "also holds none" in text  # a condition value the names do not give
    flags = " ".join(f["text"] for f in r["flags"])
    assert "scrambled guides" in flags
    import copy
    bare = copy.deepcopy(base)
    bare["perturbation"]["controls"]["value"][0].pop("description")
    bare["units"]["unit_column"]["value"] = "pool"
    bare["perturbation"]["assignment"] = {"value": {"rule": "other"}, "status": "proposed", "source": "x"}
    text = " ".join(checks.check(bare, {"facts": [], "library": lib})["problems"])
    for expected in ("needs a description of the control", "no column 'pool'", "write the calls into the cell tables"):
        assert expected in text, expected
    assert design_gate.condition_names({"column": "treatment", "names": ["drug", "vehicle"]}) == ["drug", "vehicle"]


def test_dense_tables_are_read_into_a_cell_table(tmp_path):
    rng = np.random.default_rng(1)
    genes, guides = [f"G{i}" for i in range(30)], ["A_1", "B_1", "NT_1"]
    bcs = [f"CELL{i:03d}" for i in range(50)]
    X = rng.poisson(2, (50, 30))
    Gm = rng.poisson(1, (50, 3))
    pd.DataFrame(X.T, index=genes, columns=bcs).to_csv(tmp_path / "cdna.tsv.gz", sep="\t")
    # the guide table lists the same cells in another order
    pd.DataFrame(Gm.T, index=guides, columns=bcs).iloc[:, ::-1].to_csv(tmp_path / "gdo.tsv.gz", sep="\t")
    folder = str(tmp_path / "cell_tables")
    r = ct.read_dense(folder, "S1", {"Gene Expression": str(tmp_path / "cdna.tsv.gz"),
                                     "CRISPR Guide Capture": str(tmp_path / "gdo.tsv.gz")})
    assert r["ok"], r
    a = readers.read_matrix(ct.samples(folder)[0])
    assert list(a.obs_names) == bcs and a.shape == (50, 33)
    assert (a.X[:, :30] != sp.csr_matrix(X)).nnz == 0 and (a.X[:, 30:] != sp.csr_matrix(Gm)).nnz == 0
    assert list(a.var["feature_types"][30:]) == ["CRISPR Guide Capture"] * 3
    pd.DataFrame(X, index=bcs, columns=genes).to_csv(tmp_path / "cells_by_genes.csv")  # the other orientation
    r = ct.read_dense(folder, "S2", {"Gene Expression": str(tmp_path / "cells_by_genes.csv")}, genes_on="columns")
    b = readers.read_matrix([s for s in ct.samples(folder) if s["name"] == "S2"][0])
    assert r["ok"] and (b.X != sp.csr_matrix(X)).nnz == 0
    other = pd.DataFrame(Gm.T, index=guides, columns=[f"X{i}" for i in range(50)])
    other.to_csv(tmp_path / "other_cells.tsv", sep="\t")
    bad = ct.read_dense(folder, "S3", {"Gene Expression": str(tmp_path / "cdna.tsv.gz"),
                                       "CRISPR Guide Capture": str(tmp_path / "other_cells.tsv")})
    assert not bad["ok"] and "same cells" in bad["problems"][0]


def _seurat_ready():
    import shutil
    import subprocess
    from pertura import envs
    if not shutil.which(envs.rscript()) and not os.path.exists(envs.rscript()):
        return False
    p = subprocess.run([envs.rscript(), "-e", "library(SeuratObject); library(Matrix)"], env=envs.r_env(),
                       capture_output=True, text=True)
    return p.returncode == 0


def test_a_seurat_object_is_read_into_cell_tables(tmp_path):
    import subprocess

    import pytest
    from pertura import envs
    if not _seurat_ready():
        pytest.skip("R with SeuratObject is not installed here")
    rng = np.random.default_rng(2)
    X = rng.poisson(2, (40, 20))  # cells x genes
    Gm = rng.poisson(1, (40, 3))
    scipy_io = __import__("scipy.io", fromlist=["mmwrite"])
    scipy_io.mmwrite(str(tmp_path / "rna.mtx"), sp.csr_matrix(X.T))
    scipy_io.mmwrite(str(tmp_path / "guides.mtx"), sp.csr_matrix(Gm.T))
    bcs = [f"C{i:02d}" for i in range(40)]
    sample = ["lane1"] * 25 + ["lane2"] * 15
    pd.DataFrame({"barcode": bcs, "lane": sample, "guide": [f"g{i % 3}" for i in range(40)]}).to_csv(
        tmp_path / "meta.csv", index=False)
    code = f"""
suppressPackageStartupMessages({{library(SeuratObject); library(Matrix)}})
rna <- as(readMM('{(tmp_path / "rna.mtx").as_posix()}'), 'CsparseMatrix')
gd <- as(readMM('{(tmp_path / "guides.mtx").as_posix()}'), 'CsparseMatrix')
meta <- read.csv('{(tmp_path / "meta.csv").as_posix()}', row.names = 1)
rownames(rna) <- paste0('G', 0:19); colnames(rna) <- rownames(meta)
rownames(gd) <- c('A-1', 'B-1', 'NT-1'); colnames(gd) <- rownames(meta)
obj <- CreateSeuratObject(rna, meta.data = meta)
obj[['CRISPR']] <- CreateAssay5Object(counts = gd)
saveRDS(obj, '{(tmp_path / "screen.rds").as_posix()}')
"""
    p = subprocess.run([envs.rscript(), "-e", code], env=envs.r_env(), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    folder = str(tmp_path / "cell_tables")
    out = ct.read_seurat(folder, str(tmp_path / "screen.rds"), {"RNA": "Gene Expression",
                                                                 "CRISPR": "CRISPR Guide Capture"},
                         sample_column="lane", work=str(tmp_path))
    assert [r["ok"] for r in out] == [True, True], out
    tables = {s["name"]: readers.read_matrix(s) for s in ct.samples(folder)}
    assert sorted(tables) == ["lane1", "lane2"]
    a = tables["lane1"]
    assert list(a.obs_names) == bcs[:25] and list(a.obs["guide"]) == [f"g{i % 3}" for i in range(25)]
    assert (a.X[:, :20] != sp.csr_matrix(X[:25])).nnz == 0 and (a.X[:, 20:] != sp.csr_matrix(Gm[:25])).nnz == 0
    assert ct.made_by_agent(folder) == {}  # Pertura's reader, not the agent's code


def test_crossed_condition_factors_reach_every_cell(tmp_path):
    from pertura import design_gate
    from pertura.design_audit import checks
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    tables = _tables(str(dep), str(tmp_path / "tables"))
    # each cell's level of each factor, written by sample (one value gives every cell of the sample that value)
    assert ct.annotate(tables, "host_age", {"S1": "young", "S2": "aged"})["ok"]
    assert ct.annotate(tables, "tissue", {"S1": "tumour", "S2": "tumour"})["ok"]
    factors = [{"name": "host_age", "levels": ["young", "aged"], "column": "host_age"},
               {"name": "tissue", "levels": ["tumour", "tdLN"], "column": "tissue"}]
    assert design_gate.condition_names(factors) == ["young|tumour", "young|tdLN", "aged|tumour", "aged|tdLN"]
    design = _design(_guides(str(dep)), independent_unit="donor", conditions=factors)
    so.build(str(dep), design, str(tmp_path / "screen"), cell_tables=tables)
    cells = pd.read_csv(tmp_path / "screen" / "cells.tsv.gz", sep="\t")
    assert set(zip(cells["sample"], cells["condition"])) == {("S1", "young|tumour"), ("S2", "aged|tumour")}
    assert set(cells["condition_host_age"]) == {"young", "aged"} and set(cells["condition_tissue"]) == {"tumour"}
    lib = {"samples": ["S1", "S2"], "cell_table_columns": {"host_age": ["aged", "old", "young"]},
           "cell_table_columns_by_sample": {"host_age": {"S1": {"young": 400}, "S2": {"aged": 390, "old": 10}}}}
    problems, flags = [], []
    checks._conditions({"experiment": {"conditions": {"value": factors[:1], "status": "proposed"}}}, {}, lib, problems,
                       flags)
    assert any("also holds old" in p for p in problems), problems
    assert "the column 'host_age' in each sample: S1: young 400; S2: aged 390, old 10" in [f["text"] for f in flags]
    problems = []
    checks._conditions({"experiment": {"conditions": {"value": ["untreated", "infected"], "status": "proposed"}}}, {},
                       lib, problems, [])
    assert "no column holds them" in " ".join(problems)
    # a word is no form of the conditions, and never its letters ("none" was read as e, n and o)
    problems = []
    checks._conditions({"experiment": {"conditions": {"value": "none", "status": "proposed"}}}, {}, lib, problems, [])
    assert problems == ["experiment.conditions: 'none' is a word, not one of its forms: [] where every cell had the "
                        "same treatment and time point, {column, names} for one factor, or [{name, levels, column}] "
                        "for crossed factors"], problems
    assert design_gate.condition_names("none") == ["none"]


def test_guides_in_no_target_are_said(tmp_path):
    # the corpus check's GSE327731: a design with no target map built each guide as its own target, and no flag said so
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    guides = _guides(str(dep))
    units = {"independent_unit": "donor"}
    mapped = so.build(str(dep), _design(guides, **units), str(tmp_path / "a"))
    assert all(f["flag"] != "guides in no target" for f in mapped["flags"])
    design = _design(guides, **units)
    del design["perturbation"]["target_map"]
    unmapped = so.build(str(dep), design, str(tmp_path / "b"))
    flag = next(f for f in unmapped["flags"] if f["flag"] == "guides in no target")
    targeting = [g for g in guides if not g.startswith("NTC")]
    assert (flag["guides"], flag["of"]) == (len(targeting), len(guides))
    assert "perturbation.target_map" in flag["note"]


def test_guides_a_target_map_leaves_out_read_as_no_guide(tmp_path):
    # development run on GSE203592, 2026-11-05: the deposit's zMulti-1 and zNone-1 were in no target and no control;
    # the form said their cells are left out, and the build made each a target
    dep = tmp_path / "dep"
    dep.mkdir()
    make_deposit(str(dep))
    guides = _guides(str(dep))
    units = {"independent_unit": "donor"}
    design = _design(guides, **units)
    gone = sorted(design["perturbation"]["target_map"]["value"])[0]
    out_guides = design["perturbation"]["target_map"]["value"].pop(gone)
    summary = so.build(str(dep), design, str(tmp_path / "screen"))
    flag = next(f for f in summary["flags"] if f["flag"] == "guides left out")
    assert flag["guides"] == len(out_guides) and set(flag["such as"]) <= set(out_guides)
    assert all(f["flag"] != "guides in no target" for f in summary["flags"])
    cells = pd.read_csv(tmp_path / "screen" / "cells.tsv.gz", sep="	", keep_default_na=False)
    assert not set(cells["label"]) & (set(out_guides) | {gone})
    table = pd.read_csv(tmp_path / "screen" / "guides.tsv", sep="	")
    assert not set(table["guide"]) & set(out_guides)
