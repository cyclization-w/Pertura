"""The cell table (docs/adr/0023): the one form every deposit is read into before Pertura measures it, whoever read it.

Per sample, in the project's design folder (cell_tables/):
- <sample>.h5ad: the counts (cells x features, whole numbers, CSR); var with 'gene_ids', 'name' and 'feature_types'
  (Cell Ranger's: 'Gene Expression', 'CRISPR Guide Capture', 'Antibody Capture', or another type the deposit names);
  obs indexed by the cells' barcodes, with any cell-metadata columns the deposit gives (the authors' labels, a hashtag
  or donor call, a condition);
- <sample>_protospacer_calls_per_cell.csv, where the deposit (or the reader) gives guide calls: Cell Ranger's columns
  (cell_barcode, num_features, feature_call, num_umis; several guides joined by '|');
- manifest.json: for each sample, its files, who wrote it (a Pertura reader by name, or the agent's run by ID), the
  deposit files it was made from, and when.

When the folder has a manifest, the measurement, check_design and the screen build read the deposit's samples from
it (pertura.design_audit.readers.discover). The agent writes a table with `write` from its notebook (run_code) for a
deposit Pertura's readers cannot read; `write` checks it at once and returns the problems, and a table with problems
is not entered in the manifest. Each cell's unit or condition, however the agent reads it (hashtags, a genotype
demultiplexer's output, the deposit's cell metadata, a barcode's lane), enters as a column written with `annotate`;
the design names the column.
"""
import collections
import json
import os
import time

import numpy as np
import pandas as pd
import scipy.sparse as sp

FOLDER = "cell_tables"
MANIFEST = "manifest.json"
GENE_EXPRESSION, GUIDE = "Gene Expression", "CRISPR Guide Capture"
CALL_COLUMNS = ["cell_barcode", "num_features", "feature_call", "num_umis"]


def _name_ok(name):
    return bool(name) and all(c.isalnum() or c in "-_." for c in str(name)) and not str(name).startswith(".")


def check(X, features, barcodes, calls=None, guides=None):
    """Problems that keep a table out of the manifest: counts that are not whole numbers or are negative, barcodes
    repeated, features without an ID, name and type, called guides that are neither guide features nor in `guides`,
    and calls naming cells the table does not hold. Returns a list of problems (empty when it passes)."""
    problems = []
    X = sp.csr_matrix(X)
    if X.shape != (len(barcodes), len(features)):
        problems.append(f"the counts are {X.shape[0]} x {X.shape[1]}, but there are {len(barcodes)} barcodes and "
                        f"{len(features)} features")
        return problems
    if X.nnz and (np.any(X.data < 0) or not np.all(np.mod(X.data, 1) == 0)):
        problems.append("the counts are not all whole numbers of zero or more: give counts, or turn normalised values "
                        "back into counts with a step you state")
    bc = pd.Series(barcodes, dtype=str)
    if bc.duplicated().any():
        problems.append(f"{int(bc.duplicated().sum())} barcodes repeated within the sample (e.g. "
                        f"{bc[bc.duplicated()].iloc[0]!r})")
    for col in ("gene_ids", "name", "feature_types"):
        if col not in features.columns:
            problems.append(f"the features have no '{col}' column")
        elif features[col].isna().any() or (features[col].astype(str) == "").any():
            problems.append(f"{int((features[col].isna() | (features[col].astype(str) == '')).sum())} features "
                            f"without a {col}")
    if calls is not None and len(calls):
        missing = [c for c in CALL_COLUMNS if c not in calls.columns]
        if missing:
            problems.append(f"the calls have no {', '.join(missing)} column (Cell Ranger's columns)")
        else:
            outside = ~calls["cell_barcode"].astype(str).isin(set(bc))
            if outside.any():
                problems.append(f"{int(outside.sum())} called cells are not among the table's barcodes (e.g. "
                                f"{calls['cell_barcode'].astype(str)[outside].iloc[0]!r})")
            known = set(guides or [])
            if "feature_types" in features.columns:
                known |= set(features.loc[features["feature_types"].astype(str) == GUIDE, "name"].astype(str))
            called = {g for v in calls["feature_call"].astype(str) for g in v.split("|") if g}
            unknown = sorted(called - known)
            if unknown:
                problems.append(f"{len(unknown)} called guides are neither guide features nor listed guides (e.g. "
                                f"{', '.join(unknown[:3])})")
    return problems


def write(folder, name, X, features, barcodes, calls=None, obs=None, made_by="", source_files=(), guides=None):
    """Write one sample's cell table into `folder` (the project's design/cell_tables) and enter it in the manifest.
    X: counts (cells x features); features: a DataFrame with gene_ids, name and feature_types; barcodes: the cells'
    barcodes; calls: Cell Ranger's call columns, if any; obs: cell-metadata columns indexed by barcode; made_by: the
    reader ('pertura:<reader>') or the run ('run:<ID>'); source_files: the deposit files it was read from; guides:
    guide names that are not features (calls from a separate readout). Returns {ok, problems, sample}."""
    import anndata as ad
    if not _name_ok(name):
        return {"ok": False, "problems": [f"sample name {name!r}: use letters, digits, '-', '_' or '.'"], "sample": name}
    features = pd.DataFrame(features).reset_index(drop=True)
    barcodes = [str(b) for b in barcodes]
    calls = None if calls is None else pd.DataFrame(calls)
    problems = check(X, features, barcodes, calls, guides)
    if problems:
        return {"ok": False, "problems": problems, "sample": name}
    os.makedirs(folder, exist_ok=True)
    X = sp.csr_matrix(X)
    if X.dtype.kind == "f":
        X = X.astype(np.int64)  # whole numbers, checked above
    var = features[["gene_ids", "name", "feature_types"]].astype(str).copy()
    var.index = var["name"].to_numpy()
    meta = pd.DataFrame(index=pd.Index(barcodes))
    if obs is not None:  # the cells' columns, matched by barcode
        o = pd.DataFrame(obs).copy()
        o.index = o.index.astype(str)
        o.columns = [str(c) for c in o.columns]
        for c in o.columns:  # text columns as text: h5ad cannot hold a column of mixed types
            if o[c].dtype == object or str(o[c].dtype).startswith(("string", "category")):
                o[c] = o[c].astype(str)
        meta = meta.join(o, how="left")
    a = ad.AnnData(X, obs=meta, var=var)
    a.var_names_make_unique()
    path = os.path.join(folder, f"{name}.h5ad")
    a.write_h5ad(path + ".tmp")
    os.replace(path + ".tmp", path)
    entry = {"file": os.path.basename(path), "calls": None, "made_by": made_by, "source_files": list(source_files),
             "cells": int(X.shape[0]), "features": int(X.shape[1]), "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if calls is not None and len(calls):
        cpath = os.path.join(folder, f"{name}_protospacer_calls_per_cell.csv")
        calls[CALL_COLUMNS].to_csv(cpath + ".tmp", index=False)
        os.replace(cpath + ".tmp", cpath)
        entry["calls"] = os.path.basename(cpath)
    m = manifest(folder)
    m.setdefault("samples", {})[name] = entry
    with open(os.path.join(folder, MANIFEST) + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    os.replace(os.path.join(folder, MANIFEST) + ".tmp", os.path.join(folder, MANIFEST))
    return {"ok": True, "problems": [], "sample": name}


DENSE_CHUNK = 2000  # rows of a dense table read at a time


def _dense(path, genes_on, sep):
    """(counts cells x features as CSR, feature names, barcodes) of a dense count table, read in chunks of rows."""
    if sep is None:
        base = path[:-3] if path.endswith(".gz") else path
        sep = "," if base.endswith(".csv") else "\t"
    blocks, index = [], []
    for chunk in pd.read_csv(path, sep=sep, index_col=0, chunksize=DENSE_CHUNK):
        blocks.append(sp.csr_matrix(chunk.to_numpy()))
        index += [str(x) for x in chunk.index]
        columns = [str(c) for c in chunk.columns]
    M = sp.vstack(blocks).tocsr() if blocks else sp.csr_matrix((0, 0))
    if genes_on == "rows":  # genes x cells, as most deposited tables are
        return M.T.tocsr(), index, columns
    return M, columns, index


def read_dense(folder, name, tables, genes_on="rows", sep=None, obs=None, made_by="pertura:dense_table"):
    """Pertura's reader for dense count tables (CSV or TSV, gzipped or not): one sample's readouts, each a table of
    the same cells, into its cell table. tables: {feature type: path}, e.g. {"Gene Expression": cdna.tsv.gz,
    "CRISPR Guide Capture": guide_counts.tsv.gz}; genes_on: 'rows' (features x cells, cells as columns) or 'columns';
    obs: cell-metadata columns by barcode. Which axis holds the genes, which sample a table is and which type each
    readout is are read by the agent; this code reads the numbers. Returns write()'s answer."""
    parts, barcodes, problems = [], None, []
    for ftype, path in tables.items():
        X, names, bcs = _dense(path, genes_on, sep)
        if barcodes is None:
            barcodes = bcs
        elif set(bcs) != set(barcodes):
            problems.append(f"{os.path.basename(path)} holds {len(set(bcs) ^ set(barcodes))} cells the first table "
                            "does not, or lacks some it has: the readouts must be of the same cells")
            continue
        else:
            X = X[pd.Index(bcs).get_indexer(barcodes)]
        parts.append((X, pd.DataFrame({"gene_ids": names, "name": names, "feature_types": ftype})))
    if problems:
        return {"ok": False, "problems": problems, "sample": name}
    X = sp.hstack([x for x, _ in parts]).tocsr()
    features = pd.concat([f for _, f in parts], ignore_index=True)
    return write(folder, name, X, features, barcodes, obs=obs, made_by=made_by, source_files=list(tables.values()))


def read_seurat(folder, path, assays, sample_column=None, calls_column=None, made_by="pertura:seurat", work=None):
    """Pertura's reader for a Seurat object (.rds): the counts of each assay named ({assay: feature type}, e.g.
    {"RNA": "Gene Expression", "CRISPR": "CRISPR Guide Capture"}) and the object's meta.data, exported with Seurat's
    own accessors in R (r/seurat_export.R) and written as cell tables, one per value of sample_column (a meta.data
    column naming each cell's sample), or one table without it. calls_column: a meta.data column naming each cell's
    guide (several joined by '|'), written as the table's guide calls (calls_from_column). Returns [write()'s answer
    per sample]."""
    import subprocess
    import tempfile

    import scipy.io
    from . import envs
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "r", "seurat_export.R")
    with tempfile.TemporaryDirectory(dir=work) as out:
        p = subprocess.run([envs.rscript(), script, path, out, ",".join(assays)], env=envs.r_env({"OMP_NUM_THREADS": "1"}),
                           capture_output=True, text=True)
        if p.returncode != 0:
            return [{"ok": False, "problems": [f"the R export failed: {p.stderr.strip()[-600:]}"], "sample": None}]
        parts, barcodes = [], None
        for assay, ftype in assays.items():
            m = sp.csr_matrix(scipy.io.mmread(os.path.join(out, f"{assay}_matrix.mtx")).T)
            names = open(os.path.join(out, f"{assay}_features.txt"), encoding="utf-8").read().splitlines()
            bcs = open(os.path.join(out, f"{assay}_barcodes.txt"), encoding="utf-8").read().splitlines()
            if barcodes is None:
                barcodes = bcs
            elif bcs != barcodes:
                m = m[pd.Index(bcs).get_indexer(barcodes)]
            parts.append((m, pd.DataFrame({"gene_ids": names, "name": names, "feature_types": ftype})))
        meta = pd.read_csv(os.path.join(out, "meta_data.csv"), index_col=0)
    X = sp.hstack([x for x, _ in parts]).tocsr()
    features = pd.concat([f for _, f in parts], ignore_index=True)
    meta.index = meta.index.astype(str)
    meta = meta.reindex(barcodes)
    if sample_column is None:
        groups = {os.path.basename(path).split(".")[0]: np.arange(len(barcodes))}
    elif sample_column not in meta.columns:
        return [{"ok": False, "problems": [f"the object's meta.data has no column '{sample_column}' (it has "
                                           f"{', '.join(map(str, meta.columns[:20]))})"], "sample": None}]
    else:
        groups = {str(k): np.flatnonzero(meta[sample_column].astype(str).to_numpy() == str(k))
                  for k in pd.unique(meta[sample_column].astype(str))}
    out = []
    for sample, rows in groups.items():
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in sample)
        bcs = [barcodes[i] for i in rows]
        calls = calls_from_column(meta.iloc[rows][calls_column], bcs) if calls_column else None
        out.append(write(folder, safe, X[rows], features, bcs, calls=calls, obs=meta.iloc[rows], made_by=made_by,
                         source_files=[os.path.basename(path)],
                         guides=sorted({g for v in calls["feature_call"] for g in v.split("|")}) if calls is not None
                         else None))
    return out


def calls_from_column(values, barcodes):
    """Guide calls in Cell Ranger's columns from a cell column naming each cell's guide (several joined by '|'): the
    deposit's own assignment, without UMIs. Cells with no guide named are left out."""
    v = pd.Series(list(values), dtype=object).astype(str)
    has = ~v.isin(["", "nan", "None", "NA", "<NA>"]).to_numpy()
    v = v[has]
    return pd.DataFrame({"cell_barcode": np.asarray(barcodes, dtype=object)[has],
                         "num_features": v.str.count(r"\|").to_numpy() + 1, "feature_call": v.to_numpy(),
                         "num_umis": ""})


def read_10x(folder, sample, matrix=None, calls=None, features=None, barcodes=None, declared=None,
             made_by="pertura:10x"):
    """One sample of 10x files written as a cell table: sample is either a name, with the matrix (an h5 file, or a
    matrix.mtx with its features and barcodes files) and its call file, or Pertura's readers' entry for it
    (pertura.design_audit.readers.discover: its extra readouts and paired call files included). Calls naming cells
    the matrix does not hold are left out and counted. Returns write's answer with calls_left_out."""
    from .design_audit.readers import MTX, read_calls, read_matrix
    if not isinstance(sample, dict):
        kind = "h5" if str(matrix).endswith(".h5") else "mtx"
        sample = {"name": sample, "kind": kind, "matrix": matrix, **({"calls": calls} if calls else {}),
                  **({"features": features, "barcodes": barcodes} if kind == "mtx" else {})}
    a = read_matrix(sample, declared)
    calls, left, guides = None, 0, None
    if sample.get("calls"):
        calls = read_calls(sample["calls"])
        inside = calls["cell_barcode"].astype(str).isin(set(map(str, a.obs_names)))
        calls, left = calls[inside], int((~inside).sum())
        guides = sorted({g for v in calls["feature_call"].astype(str) for g in v.split("|") if g})
    def files(s):  # a triplet's three files, its matrix found from a prefix as the reader finds it
        if s.get("features") is None:
            return [s["matrix"]]
        m = s["matrix"] if s["matrix"].endswith(MTX) else next(
            (s["matrix"] + x for x in MTX if os.path.exists(s["matrix"] + x)), s["matrix"])
        return [m, s["features"], s["barcodes"]]
    given = sample.get("calls")
    sources = [f for s in [sample, *sample.get("extra", [])] for f in files(s)] + (
        [given] if isinstance(given, str) else [c["path"] for c in given or []])
    out = write(folder, sample["name"], a.X, a.var, list(a.obs_names), calls=calls, made_by=made_by,
                source_files=sources, guides=guides)
    return {**out, "calls_left_out": left}


def propose(dep, folder, declared=None):
    """Where the project has no cell tables yet, the deposit's samples as Pertura's readers find them (dep:
    readers.discover's answer) written as cell tables: a proposed reading, which the agent checks and rewrites where
    it is wrong (docs/adr/0041, step 1b). What the readers decided stands in the manifest's notes: the files not read
    as samples (a raw matrix beside its filtered one, the same cells in another file, a guide readout of few features)
    and those they could not open. Captures of one matrix told apart by the barcode prefixes of their call files get
    each cell's capture as the column 'capture'. Returns the manifest, or None where tables exist or no matrix is
    read."""
    from .design_audit.audit import guide_readouts, why_guide_readout
    if samples(folder) or dep.get("layout") == "cell_tables" or not dep.get("samples"):
        return None
    readouts = guide_readouts(dep["samples"])
    notes = [f"{d['matrix']}: not read, " + (d.get("same_capture_as") or f"the same cells as {d.get('same_cells_as')}")
             for d in dep.get("duplicates") or []]
    notes += [f"{os.path.basename(s['matrix'])}: not read as cells, a guide readout "
              f"({why_guide_readout(s, dep['samples'])}); where it holds this screen's guides, write them into the "
              "samples whose cells it reads" for s in readouts]
    notes += [f"{os.path.basename(u['file'])}: could not be opened ({u['error']})" for u in dep.get("unreadable") or []]
    notes += [f"{e['matrix']}: not read, a matrix of every droplet ({e['barcodes_in_one_lane']:,} barcodes in one lane)"
              for e in dep.get("every_droplet") or []]
    notes += [f"{os.path.basename(e['matrix'])}: read with {s['name']} as further features of the same cells (the same "
              "barcodes); where it is a second library of the same features, read one of them" for s in dep["samples"]
              for e in s.get("extra", [])]
    for s in dep["samples"]:
        if s in readouts:
            continue
        r = read_10x(folder, s, declared)
        if not r["ok"]:
            notes.append(f"{s['name']}: not written ({'; '.join(r['problems'])})")
            continue
        if r["calls_left_out"]:
            notes.append(f"{s['name']}: {r['calls_left_out']} calls name cells its matrix does not hold; left out")
        prefixes = sorted({c["prefix"] for c in s["calls"] if c.get("prefix")}, key=len, reverse=True) \
            if isinstance(s.get("calls"), list) else []
        if prefixes:  # captures of one matrix, each with its call file, told apart by the barcode prefixes
            import anndata as ad
            t = ad.read_h5ad(os.path.join(folder, f"{s['name']}.h5ad"), backed="r")
            bcs = t.obs_names.astype(str)
            t.file.close()  # annotate rewrites the file
            annotate(folder, "capture", {s["name"]: [next((p.rstrip("_:-") for p in prefixes if b.startswith(p)), "")
                                                     for b in bcs]},
                     made_by="pertura:discover", note="each cell's capture, from the barcode prefixes of its call files")
    m = manifest(folder)
    m.update(proposed_by="pertura:discover", notes=notes + m.get("notes", []))  # with the columns written above
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MANIFEST) + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    os.replace(os.path.join(folder, MANIFEST) + ".tmp", os.path.join(folder, MANIFEST))
    return m


def retype(folder, name, features, feature_type=GUIDE, made_by=""):
    """Change what some features of a sample's table are, as the agent reads them: features, a list of names, or
    {name: new name} (a guide row of the expression matrix read as the library's guide of another spelling);
    feature_type, the Cell Ranger type they take: guides read from the expression take 'CRISPR Guide Capture', rows
    that are neither genes nor this sample's guides another type (such as 'Guide read in the expression'), which
    leaves them out of the genes. The table is rewritten in place, its calls kept, and the change noted in the
    manifest. Returns {ok, problems, sample, retyped}."""
    import anndata as ad
    m = manifest(folder)
    e = m.get("samples", {}).get(name)
    if e is None:
        return {"ok": False, "problems": [f"no sample {name!r} in the manifest"], "sample": name}
    rename = {str(k): str(v) for k, v in features.items()} if isinstance(features, dict) else {
        str(f): str(f) for f in features}
    path = os.path.join(folder, e["file"])
    a = ad.read_h5ad(path)
    names = a.var["name"].astype(str).to_numpy()
    missing = sorted(set(rename) - set(names))
    if missing:
        return {"ok": False, "problems": [f"{len(missing)} features are not in the table (e.g. {missing[0]})"],
                "sample": name}
    hit = np.isin(names, list(rename))
    a.var["feature_types"] = np.where(hit, feature_type, a.var["feature_types"].astype(str).to_numpy())
    a.var["name"] = [rename[x] if h else x for x, h in zip(names, hit)]
    a.var.index = a.var["name"].to_numpy()
    a.var_names_make_unique()
    a.write_h5ad(path + ".tmp")
    os.replace(path + ".tmp", path)
    m.setdefault("notes", []).append(f"{name}: {int(hit.sum())} features read as {feature_type}"
                                     + (f", by {made_by}" if made_by else ""))
    with open(os.path.join(folder, MANIFEST) + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    os.replace(os.path.join(folder, MANIFEST) + ".tmp", os.path.join(folder, MANIFEST))
    return {"ok": True, "problems": [], "sample": name, "retyped": int(hit.sum())}


EMPTY = ("", "nan", "None", "NA", "<NA>")  # a cell's value that says it has none


def annotate(folder, column, values, made_by="", note=""):
    """Write a column into the cells of the samples' tables, as the agent reads each cell's unit or condition
    (docs/adr/0041, step 4): values is {sample: the cells' values}, each a Series indexed by the table's barcodes, a
    list in the table's cell order, or one value for every cell of the sample (a condition given by sample); a cell
    the values do not name gets none (''). Each distinct value is a unit or level of its own: a cell that belongs to
    none (its donor or mouse not told) is left without a value, which the build reads as no unit, never a word such as
    'unassigned', which would become a unit (GSE190604: a third "donor" of unassigned cells). The tables are rewritten
    in place, and the column noted in the manifest with who wrote it and how many cells have no value. Returns {ok,
    problems, column, samples: {name: {cells, without_value}}, values: the distinct values, up to 50, cells_per_value,
    read_as}."""
    import anndata as ad
    if not _name_ok(column):
        return {"ok": False, "problems": [f"column name {column!r}: use letters, digits, '-', '_' or '.'"],
                "column": column}
    m = manifest(folder)
    have = m.get("samples", {})
    if not have:  # a folder that holds no cell tables (the screen object's, the deposit's)
        return {"ok": False, "problems": [f"{folder} holds no cell tables; the project's are the notebook's "
                                          "cell_tables"], "column": column}
    unknown = sorted(set(map(str, values)) - set(have))
    if unknown:
        return {"ok": False, "problems": [f"no sample {', '.join(unknown[:5])} in the manifest "
                                          f"({', '.join(sorted(have))})"], "column": column}
    done, seen, problems, counts = {}, set(), [], collections.Counter()
    for name, v in values.items():
        path = os.path.join(folder, have[str(name)]["file"])
        a = ad.read_h5ad(path)
        bcs = a.obs_names.astype(str)
        if isinstance(v, (str, int, float)):
            v = [v] * a.n_obs
        if isinstance(v, pd.Series):
            v = v.copy()
            v.index = v.index.astype(str)
            if len(v) and not v.index.isin(bcs).any():  # most often a barcode written another way ('-1' or not)
                problems.append(f"{name}: none of the values' barcodes is among the table's (e.g. {v.index[0]!r} "
                                f"against {bcs[0]!r})")
                continue
            v = v.reindex(bcs)
        elif len(v) != a.n_obs:
            problems.append(f"{name}: {len(v)} values for {a.n_obs} cells; give them in the table's cell order, or as a "
                            "Series indexed by barcode")
            continue
        text = pd.Series(np.asarray(v, dtype=object), index=bcs)
        text = text.where(text.notna(), "").astype(str)  # pandas 3's astype(str) keeps a missing value as NaN
        text[text.isin(EMPTY)] = ""
        a.obs[str(column)] = text.to_numpy()
        a.write_h5ad(path + ".tmp")
        os.replace(path + ".tmp", path)
        done[str(name)] = {"cells": int(a.n_obs), "without_value": int((text == "").sum())}
        seen |= set(text[text != ""].unique()[:51])
        counts.update(text[text != ""].value_counts().to_dict())
    if done:
        m.setdefault("notes", []).append(
            f"{column}: written into {', '.join(done)}" + (f" by {made_by}" if made_by else "") + (f" ({note})" if note
                                                                                                  else "")
            + "; cells without a value: " + ", ".join(f"{n} {d['without_value']} of {d['cells']}" for n, d in
                                                      done.items()))
        with open(os.path.join(folder, MANIFEST) + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=1)
        os.replace(os.path.join(folder, MANIFEST) + ".tmp", os.path.join(folder, MANIFEST))
    return {"ok": not problems, "problems": problems, "column": column, "samples": done,
            "values": sorted(seen)[:50], "cells_per_value": dict(counts.most_common(50)),
            "read_as": "each value is one unit or level; a cell without a value has none (as a unit: no replicate)"}


def remove(folder, name, why, made_by=""):
    """Take a sample out of the reading: out of the manifest, with why in its notes (a second file of one capture's
    cells, a matrix of every droplet). Its table stays on disk. Returns {ok, sample}."""
    m = manifest(folder)
    if name not in m.get("samples", {}):
        have = ", ".join(sorted(m.get("samples", {})))
        return {"ok": False, "problems": [f"no sample {name!r} in the manifest ({have})"], "sample": name}
    del m["samples"][name]
    m.setdefault("notes", []).append(f"{name}: not read ({why})" + (f", by {made_by}" if made_by else ""))
    with open(os.path.join(folder, MANIFEST) + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    os.replace(os.path.join(folder, MANIFEST) + ".tmp", os.path.join(folder, MANIFEST))
    return {"ok": True, "problems": [], "sample": name}


def reading(folder):
    """How the deposit was read, for the agent's replies and the user's form: each sample with who read it and from
    which files, and the manifest's notes; None without tables."""
    m = manifest(folder)
    if not m.get("samples"):
        return None
    return {"proposed_by": m.get("proposed_by"), "notes": m.get("notes") or [],
            "samples": {n: {"read_by": e.get("made_by"), "cells": e.get("cells"),
                            "from": [os.path.basename(f) for f in e.get("source_files") or []]}
                        for n, e in sorted(m["samples"].items())}}


def manifest(folder):
    """The folder's manifest ({"samples": {name: entry}}), or an empty one."""
    path = os.path.join(folder, MANIFEST)
    if not os.path.exists(path):
        return {"samples": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def samples(folder):
    """The manifest's samples as the deposit readers' sample entries (kind 'cell_table'), or [] without a manifest."""
    if not folder:
        return []
    out = []
    for name, e in sorted(manifest(folder).get("samples", {}).items()):
        s = {"name": name, "kind": "cell_table", "matrix": os.path.join(folder, e["file"]),
             "made_by": e.get("made_by", "")}
        if e.get("calls"):
            s["calls"] = os.path.join(folder, e["calls"])
        out.append(s)
    return out


def made_by_agent(folder):
    """{sample: run} for the manifest's samples the agent's own code read."""
    return {n: e.get("made_by", "") for n, e in manifest(folder).get("samples", {}).items()
            if str(e.get("made_by", "")).startswith("run:")}
