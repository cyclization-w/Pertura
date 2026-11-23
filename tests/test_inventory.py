"""The inventory detectors (pertura.design_audit.inventory) on small synthetic deposits, one trap each."""
import gzip
import json
import os
import tarfile

import numpy as np
import pandas as pd
import scipy.sparse as sp

from pertura.design_audit import inventory as inv
from pertura.design_audit import readers


def keys(facts):
    return {f["key"] for f in facts}


def test_raw_droplets_are_flagged_and_filtered_matrices_are_not():
    rng = np.random.default_rng(0)
    raw = np.concatenate([rng.integers(0, 20, 90_000), rng.integers(1000, 5000, 5000)])
    f, q = inv.raw_droplets({"lane1": raw, "lane2": rng.integers(1000, 5000, 8000)})
    assert keys(f) == {"data.raw_droplets"} and list(f[0]["value"]) == ["lane1"] and q


def test_matrix_content_tells_counts_log_counts_and_normalised():
    rng = np.random.default_rng(1)
    counts = rng.poisson(1.0, (200, 50)).astype(float)
    assert inv.matrix_content(counts)[0][0]["value"] == "counts"
    f, q = inv.matrix_content(np.log1p(counts), {"n_counts": counts.sum(1)}, marked_log=True)
    assert f[0]["value"] == "log(1 + counts)" and "n_counts" in f[0]["note"]
    assert "data.matrix_content_vs_metadata" in keys(f) and q
    norm = counts / counts.sum(1, keepdims=True) * 1e4
    assert inv.matrix_content(np.log1p(norm))[0][0]["value"].startswith("log(1 + normalised")


def test_guides_typed_as_genes_are_read_as_guides(tmp_path):
    d = tmp_path / "dep"
    d.mkdir()
    sp_m = sp.coo_matrix(np.array([[5, 0], [0, 7], [1, 1]]))  # features x cells
    import scipy.io
    scipy.io.mmwrite(str(d / "x_matrix.mtx"), sp_m)
    (d / "x_features.tsv").write_text("GENE1\tGENE1\tGene Expression\ng1\tg1\tGene Expression\n"
                                      "g2\tg2\tGene Expression\n")
    (d / "x_barcodes.tsv").write_text("AAA-1\nCCC-1\n")
    ref = pd.DataFrame({"id": ["g1", "g2"], "name": ["g1", "g2"], "feature_type": [readers.GUIDE] * 2})
    s = readers.discover(str(d))["samples"][0]
    a = readers.read_matrix(s, readers.declared_types(ref))
    assert list(a.var["feature_types"]) == ["Gene Expression", readers.GUIDE, readers.GUIDE]
    f, q = inv.type_conflicts(a.uns["type_conflicts"])
    assert f and f[0]["value"][0]["n"] == 2 and q


def test_unannotated_features_that_behave_like_guides():
    rng = np.random.default_rng(2)
    ids = [f"ENSMUSG{n:011d}" for n in range(300)] + ["Ctrl_gRNA", "Myrf_gRNA", "Sox2_gRNA"]
    names = ids[:300] + ["Ctrl_gRNA", "Myrf_gRNA", "Sox2_gRNA"]
    X = rng.poisson(0.5, (400, 303)).astype(float)
    X[:, 300:] = 0
    for i in range(400):
        X[i, 300 + i % 3] = 5  # each cell carries one of the three
    f, q = inv.unannotated_features(ids, names, sp.csr_matrix(X))
    assert f[0]["value"]["n"] == 3 and f[0]["value"]["one_dominates_share"] == 1.0 and q


def test_guide_reliability_flags_few_readable_cells_low_umis_and_ambient_differences():
    cells = [f"c{i}" for i in range(400)]
    unit = ["a"] * 200 + ["b"] * 200
    calls = {}
    for i in range(0, 200, 10):
        calls[f"c{i}"] = [("g1", 1)]
    for i in range(200, 400, 10):
        calls[f"c{i}"] = [("g1", 1), ("g2", 1)]
    f, q = inv.guide_reliability(cells, unit, np.array([1 if c in calls else 0 for c in cells]), calls, {})
    k = keys(f)
    assert {"perturbation.readable_fraction", "perturbation.cells_without_guide", "perturbation.call_umis",
            "perturbation.ambient_guides_differ"} <= k


def test_cooccurrence_finds_vectors_sets_doublets_and_typos():
    guides = ["mitfa_1", "tfec_1", "erf_1", "erfl3_1", "ragra", "rarga", "A_1", "A_2"]
    target_of = {"mitfa_1": "mitfa", "tfec_1": "tfec", "erf_1": "erf", "erfl3_1": "erfl3", "ragra": "ragra",
                 "rarga": "rarga", "A_1": "A", "A_2": "A"}
    rows = []
    rows += [[1, 1, 0, 0, 0, 0, 0, 0]] * 60   # set mitfa+tfec
    rows += [[0, 0, 1, 1, 0, 0, 0, 0]] * 60   # set erf+erfl3
    rows += [[1, 1, 1, 1, 0, 0, 0, 0]] * 15   # doublets of the two sets
    rows += [[0, 0, 0, 0, 1, 1, 0, 0]] * 40   # one guide, two names
    rows += [[0, 0, 0, 0, 0, 0, 1, 1]] * 40   # a two-guide vector of one target
    A = sp.csr_matrix(np.array(rows))
    umis = np.array([1000] * 120 + [2200] * 15 + [1000] * 80)
    f, q = inv.cooccurrence(A, guides, target_of, umis)
    k = keys(f)
    assert {"perturbation.guide_name_typos", "perturbation.delivered_sets", "perturbation.cross_set_doublets"} <= k
    sets = next(x["value"] for x in f if x["key"] == "perturbation.delivered_sets")
    assert ["mitfa", "tfec"] in sets and ["erf", "erfl3"] in sets
    dbl = next(x["value"] for x in f if x["key"] == "perturbation.cross_set_doublets")
    assert dbl["cells"] == 15 and dbl["umis_vs_one_set"] > 1.8


def test_unit_similarity_finds_one_animal_counted_twice():
    rng = np.random.default_rng(3)
    base = rng.integers(0, 50, 30).astype(float)
    base[3] = 900  # a clonal expansion
    counts = pd.DataFrame([base, base * 0.95 + 1, rng.integers(0, 50, 30), rng.integers(0, 50, 30)],
                          index=["11_1", "11_2", "12", "13"])
    f, q = inv.unit_similarity(counts, "mice")
    assert f and f[0]["value"]["pairs"][0][:2] == ["11_1", "11_2"] and q


def test_hashtags_within_lanes():
    unit = ["L1"] * 100 + ["L2"] * 100
    tag = ["H1"] * 50 + ["H2"] * 50 + ["H3"] * 100
    f, _ = inv.hashtags_within_units(unit, tag, "lanes")
    assert f and f[0]["value"]["groups"] == {"L1": ["H1", "H2"]}


def test_control_bursts_and_spread():
    counts = pd.DataFrame({"NT1": [62, 5], "NT2": [8, 30], "NT3": [9, 28]}, index=["m13", "m11"])
    f, q = inv.control_spread(counts, "mice")
    k = keys(f)
    assert "perturbation.control_bursts" in k and "m13" in next(x["value"] for x in f
                                                                 if x["key"] == "perturbation.control_bursts")


def test_testable_pairs_need_both_singles_in_one_unit():
    labels = (["A"] * 20 + ["B"] * 20 + ["A|B"] * 20 + ["C"] * 20) + (["C|D"] * 20 + ["D"] * 20 + ["E|A"] * 20
                                                                       + ["E"] * 20)
    unit = ["u1"] * 80 + ["u2"] * 80
    f, q = inv.testable_pairs(labels, unit, "units")
    v = f[0]["value"]
    assert v["pairs"] == 3 and v["testable"] == 1 and set(v["untestable"]) == {"C|D", "E|A"} and q


def test_label_disagreements_between_reference_and_cell_table():
    ref = {"T1_1": "T1", "T1_2": "T1", "INT1": "Non-Targeting", "INT2": "Non-Targeting"}
    table = pd.DataFrame({"guide": ["T1_1"] * 5 + ["T1_2"] * 5 + ["INT1"] * 5 + ["INT2"] * 5,
                          "target": ["T1"] * 10 + ["INTERGENIC"] * 10})
    f, q = inv.label_disagreements(ref, table, "guide")
    assert f and f[0]["value"]["Non-Targeting"]["table_labels"] == ["INTERGENIC"] and len(q) == 1


def test_cell_metadata_finds_guide_and_label_columns():
    bcs = [f"D1_B{i}-1" for i in range(50)]
    table = pd.DataFrame({"cell": bcs, "batch": ["b2"] * 50,
                          "guide_group": ["A-a1|NTC-1"] * 25 + ["B-i1|A-a1"] * 25,
                          "guide_group2": ["A-a"] * 25 + ["B-i|A-a"] * 25})
    f, q, t, g, lab = inv.cell_metadata(table, bcs, ["A-a1", "B-i1", "NTC-1"])
    assert g == ["guide_group"] and lab == ["guide_group2"] and q and t.index[0] == "D1_B0-1"


def test_aggregation_order_against_measured_suffixes():
    t = pd.DataFrame({"sample_id": ["MB1", "MB3", "MB2"]})
    calls = [{"path": "/x/WT_MB1_calls.csv.gz", "suffix": "1"}, {"path": "/x/WT_MB2_calls.csv.gz", "suffix": "3"},
             {"path": "/x/WT_MB3_calls.csv.gz", "suffix": "2"}]
    f, _ = inv.aggregation_order(t, "sample_id", calls)
    assert f[0]["value"]["agrees_with_calls"] and "MB1, MB3, MB2" in f[0]["note"]


def test_factor_confounding_finds_pairs_confined_to_one_batch():
    uf = pd.DataFrame({"library": ["singles", "singles", "singles", "pairs", "pairs", "pairs"],
                       "batch": ["b1", "b1", "b2", "b2", "b2", "b2"]}, index=["S1", "S2", "S3", "D1", "D2", "D3"])
    f, q = inv.factor_confounding(uf)
    nested = f[0]["value"]["nested"]
    assert {"factor": "library", "level": "pairs", "only_under": "batch", "of_that": "b2"} in nested and q


def test_samples_inventory_lists_samples_without_matrices_and_no_guide_data():
    files = ["/d/GSM1_a_matrix.mtx.gz", "/d/GSM1_a_barcodes.tsv.gz", "/d/GSM2_b_barcodes.tsv.gz"]
    geo = {"samples": {"GSM1": {"title": "a"}, "GSM2": {"title": "b"}}}
    f, q = inv.samples_inventory(files, geo, has_guide_data=False)
    k = keys(f)
    assert {"data.samples_without_matrix", "perturbation.no_guide_data"} <= k and len(q) == 2


def test_archives_are_extracted_each_into_its_own_folder(tmp_path):
    inner_dir = tmp_path / "build"
    for name in ("GSM1_B1_filtered_feature_bc_matrix", "GSM2_B2_filtered_feature_bc_matrix"):
        d = inner_dir / name / "filtered_feature_bc_matrix"
        d.mkdir(parents=True)
        (d / "matrix.mtx.gz").write_bytes(gzip.compress(b"%%MatrixMarket matrix coordinate integer general\n1 1 0\n"))
        with tarfile.open(inner_dir / f"{name}.tar.gz", "w:gz") as tf:
            tf.add(d, arcname="filtered_feature_bc_matrix")
    outer = tmp_path / "dep" / "GSE1_RAW.tar"
    outer.parent.mkdir()
    with tarfile.open(outer, "w") as tf:
        for name in ("GSM1_B1_filtered_feature_bc_matrix", "GSM2_B2_filtered_feature_bc_matrix"):
            tf.add(inner_dir / f"{name}.tar.gz", arcname=f"{name}.tar.gz")
    folders = readers.extract_archives([str(outer)], str(tmp_path / "x"))
    files = sorted(os.path.relpath(os.path.join(d, f), folders[0]) for d, _, fs in os.walk(folders[0]) for f in fs)
    assert any(f.startswith(os.path.join("GSM1_B1", "filtered_feature_bc_matrix")) for f in files)
    assert any(f.startswith(os.path.join("GSM2_B2", "filtered_feature_bc_matrix")) for f in files)
    marker = json.loads(open(os.path.join(folders[0], readers.EXTRACTED_MARKER)).readline())
    assert len(marker["inner"]) == 2
    assert readers.extract_archives([str(outer)], str(tmp_path / "x")) == folders  # not extracted twice


def _mac_sample_archive(path, build, name, stem):
    """A per-sample archive as a Mac's tar writes it (built in the folder build): the 10x triplet and Cell Ranger's
    calls in a folder, each file with an AppleDouble file beside it, and a __MACOSX folder."""
    src = build / name / name
    src.mkdir(parents=True)
    bcs = [f"{stem}{i:04d}-1" for i in range(30)]
    (src / "barcodes.tsv.gz").write_bytes(gzip.compress(("\n".join(bcs) + "\n").encode()))
    (src / "features.tsv.gz").write_bytes(gzip.compress(b"G1\tG1\tGene Expression\nG2\tG2\tGene Expression\n"))
    entries = "".join(f"{1 + i % 2} {i + 1} 3\n" for i in range(30))
    (src / "matrix.mtx.gz").write_bytes(gzip.compress(
        f"%%MatrixMarket matrix coordinate integer general\n2 30 30\n{entries}".encode()))
    rows = ["cell_barcode,num_features,feature_call,num_umis"] + [f"{b},1,g{i % 3},5" for i, b in enumerate(bcs)]
    (src / "protospacer_calls_per_cell.csv").write_text("\n".join(rows) + "\n")
    for f in list(src.iterdir()):
        (src / f"._{f.name}").write_bytes(b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        ")
    mac = build / name / "__MACOSX"
    mac.mkdir()
    (mac / "._x").write_bytes(b"\x00\x05\x16\x07")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(src, arcname=name)
        tf.add(mac, arcname="__MACOSX")


def test_archives_made_on_a_mac_read_without_their_metadata_files(tmp_path):
    dep = tmp_path / "dep"
    dep.mkdir()
    for name, stem in (("Young1", "AAAC"), ("Aged1", "CCCG")):
        _mac_sample_archive(dep / f"{name}.tar.gz", tmp_path / "build", f"scPerturb_{name}", stem)
    d, extracted, _ = readers.open_deposit(str(dep), str(tmp_path / "x"))
    assert d["layout"] == "per_sample_mtx" and len(d["samples"]) == 2
    assert all(s.get("calls") for s in d["samples"])
    unpacked = [f for e in extracted for _, _, fs in os.walk(e) for f in fs]
    assert not any(readers.mac_metadata(f) for f in unpacked) and "matrix.mtx.gz" in unpacked
    by_hand = tmp_path / "by_hand"  # unpacked with the metadata files, as a plain tar does
    for a in dep.glob("*.tar.gz"):
        with tarfile.open(a) as tf:
            tf.extractall(by_hand / a.name[:-7], filter="data")
    d = readers.discover(str(by_hand))
    assert d["layout"] == "per_sample_mtx" and len(d["samples"]) == 2


def test_text_matrices_are_parsed_once_and_read_from_the_parsed_folder(tmp_path, monkeypatch):
    dep = tmp_path / "dep"
    dep.mkdir()
    for name, stem in (("Young1", "AAAC"), ("Aged1", "CCCG"), ("Aged2", "GGGT")):
        _mac_sample_archive(dep / f"{name}.tar.gz", tmp_path / "build", f"scPerturb_{name}", stem)
    d, _, _ = readers.open_deposit(str(dep), str(tmp_path / "x"))
    parsed = str(tmp_path / "parsed")
    monkeypatch.setattr(readers, "PARALLEL_FROM_BYTES", 0)  # small files, parsed by worker processes all the same
    assert sorted(readers.parse_matrices(d["samples"], {}, parsed, workers=2)) == sorted(s["name"] for s in d["samples"])
    assert readers.parse_matrices(d["samples"], {}, parsed, workers=2) == []  # not parsed twice
    s = d["samples"][0]
    fresh, kept = readers._parse_matrix(s, {}), readers.read_matrix(s, {}, parsed)
    assert (fresh.X != kept.X).nnz == 0 and list(fresh.obs_names) == list(kept.obs_names)
    assert fresh.var.equals(kept.var)
    os.utime(s["barcodes"], (1, 1))  # a changed file is parsed again
    assert not os.path.exists(readers.parsed_path(s, {}, parsed))


def test_archives_extracted_before_are_read_without_extracting_more(tmp_path):
    dep = tmp_path / "dep"
    dep.mkdir()
    _mac_sample_archive(dep / "Young1.tar.gz", tmp_path / "build", "scPerturb_Young1", "AAAC")
    readers.open_deposit(str(dep), str(tmp_path / "x"))
    _mac_sample_archive(dep / "Aged1.tar.gz", tmp_path / "build", "scPerturb_Aged1", "CCCG")  # arrives later
    d, extracted, _ = readers.open_deposit(str(dep), str(tmp_path / "x"), extract=False)
    assert [os.path.basename(e) for e in extracted] == ["Young1"] and len(d["samples"]) == 1
    assert [os.path.basename(a) for a in d["archives"]] == ["Aged1.tar.gz"]  # listed, not extracted
    assert not os.path.exists(tmp_path / "x" / "Aged1")


def test_call_files_pair_with_a_prefixed_combined_matrix(tmp_path):
    d = tmp_path / "dep"
    d.mkdir()
    bcs = [f"D1_AAAC{i:04d}-1" for i in range(40)] + [f"S1_CCCG{i:04d}-1" for i in range(40)]
    (d / "x_barcodes.tsv").write_text("\n".join(bcs) + "\n")
    calls = []
    for s, stem in (("D1", "AAAC"), ("S1", "CCCG")):
        rows = ["cell_barcode,num_features,feature_call,num_umis"] + [f"{stem}{i:04d}-1,1,g1,5" for i in range(45)]
        (d / f"{s}_protospacer_calls_per_cell.csv").write_text("\n".join(rows) + "\n")
        calls.append(str(d / f"{s}_protospacer_calls_per_cell.csv"))
    paired = readers._pair_by_barcodes({"barcodes": str(d / "x_barcodes.tsv")}, calls)
    assert sorted(c["prefix"] for c in paired) == ["D1_", "S1_"]
    t = readers.read_calls(paired)
    assert t["cell_barcode"].isin(bcs).sum() == 80


def test_fixed_guide_pairs_without_a_target_map():
    guides = [f"T{t}-{k}" for t in range(10) for k in (1, 2)]
    rows = []
    for t in range(10):
        row = np.zeros(20)
        row[2 * t], row[2 * t + 1] = 1, 1
        rows += [row] * 25
    rows += [np.eye(20)[i] for i in range(20)] * 3
    f, q = inv.cooccurrence(sp.csr_matrix(np.array(rows)), guides, {})
    fp = next(x for x in f if x["key"] == "perturbation.guide_pairs")
    assert fp["value"]["guides_in_fixed_pairs"] == 20 and any(x["key"] == "perturbation.vector_pairs" for x in q)


def test_a_characteristic_with_one_level_per_matrix():
    geo = {"samples": {"a": {"characteristics": {"genotype": ["WT"], "cell": ["K562"]}},
                       "b": {"characteristics": {"genotype": ["KO"], "cell": ["K562"]}}}}
    f, q = inv.characteristic_per_matrix(geo, 2)
    assert f and list(f[0]["value"]) == ["genotype"] and q
    assert inv.characteristic_per_matrix(geo, 3) == ([], [])


def test_library_against_the_genes_the_metadata_name():
    geo = {"series": {"title": "screen of Clu, Myrf and Gfap", "summary": ["targets Trem2 and Fasn"],
                      "overall_design": []}}
    f, q = inv.library_against_metadata(["Clu_gRNA", "Gfap_gRNA", "Trem2_gRNA", "Fasn_gRNA"],
                                        {"Clu", "Myrf", "Gfap", "Trem2", "Fasn"}, geo)
    assert f[0]["value"]["named_in_metadata_not_in_guides"] == ["Myrf"] and q


def test_a_gene_sharing_a_guides_name_stays_a_gene(tmp_path):
    d = tmp_path / "dep"
    d.mkdir()
    import scipy.io
    scipy.io.mmwrite(str(d / "x_matrix.mtx"), sp.coo_matrix(np.array([[5, 1], [0, 7]])))
    (d / "x_features.tsv").write_text("ENSG0001\tSYVN1\tGene Expression\nSYVN1\tSYVN1\tCRISPR Guide Capture\n")
    (d / "x_barcodes.tsv").write_text("AAA-1\nCCC-1\n")
    ref = pd.DataFrame({"id": ["SYVN1"], "name": ["SYVN1"], "feature_type": [readers.GUIDE]})
    a = readers.read_matrix(readers.discover(str(d))["samples"][0], readers.declared_types(ref))
    assert list(a.var["feature_types"]) == ["Gene Expression", readers.GUIDE] and "type_conflicts" not in a.uns


def test_guides_by_sample_gives_numbers_and_asks_nothing():
    lib_a = [f"A{i}" for i in range(6)]
    lib_b = [f"B{i}" for i in range(4)]
    counts = pd.DataFrame(0, index=["s1", "s2", "s3", "s4"], columns=lib_a + lib_b + ["shared"])
    counts.loc[["s1", "s2"], lib_a + ["shared"]] = 20
    counts.loc[["s3", "s4"], lib_b + ["shared"]] = 20
    counts.loc["s4", "A0"] = 2  # below the floor: not held
    facts, qs = inv.guides_by_sample(counts)
    assert qs == []
    v = facts[0]["value"]
    assert facts[0]["key"] == "data.guides_by_sample"
    assert v["guides_held"] == {"s1": 7, "s2": 7, "s3": 5, "s4": 5}
    assert sorted(v["groups_with_the_same_set"]) == [["s1", "s2"], ["s3", "s4"]]
    assert v["pairs"][0]["shared"] == 1 and v["pairs"][-1]["shared"] in (5, 7)  # the least shared first
    assert {"samples": ["s1", "s2"], "shared": 7, "only_first": 0, "only_second": 0} in v["pairs"]
    assert "screen" not in json.dumps(facts)
    assert inv.guides_by_sample(counts.iloc[:1]) == ([], [])
