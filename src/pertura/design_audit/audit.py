"""All detectors on one deposit: design facts in the pertura.design/v1 layout, with the questions left open.

audit(root) reads the deposit (readers.discover), then:
- gene expression, guide barcodes and other barcodes (by their declared feature types) of every sample;
- guide calls from the call files, or, without them, Cell Ranger's own method on the guide UMIs, one sample at a
  time, as the build calls them by default (status inferred, and a question);
- each cell's target from the calls and the feature reference's target map, when it gives one: one target, or none
  (no call, or guides of two or more targets). Without a map, each guide stands for itself;
- the detectors (detectors.py) over the candidate units the files show: the samples, or the barcode suffixes of a
  combined matrix (lanes), and the hashtag groups when some features behave like hashtags (a group of their own, or
  a few among surface proteins): each cell's hashtag by Seurat's HTODemux and MULTIseqDemux at their defaults
  (pertura.hashtags), what each keeps, and each hashtag's share per sample, and per lane where one matrix holds
  several;
- the map-free arrays that check_design needs to measure the agent's proposed target map: each cell's called guides,
  each cell's candidate units, and per guide, over the expression subset's cells carrying it, the summed CP10k and
  the detection count of every gene detected in at least 2% of the subset's cells.
A processed object (h5ad) is read for its cell metadata: which columns hold the deposit's guide names, and which of
their values are not guides. Archives are listed with a request to extract them.
The result is {design, facts, questions, library, arrays}; write_design() saves the design as YAML, save_arrays()
the arrays. The library (guide names, cells per guide, sample names, hashtag-like features, the reference's map) and
the arrays are what the proposal checks need (checks.py).
"""
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

from ..most_common import most_common_value
from . import detectors as dt
from . import inventory as inv
from .readers import (GUIDE, _files, declared_types, discover, open_deposit, parse_matrices, read_calls,
                      read_feature_reference, read_geo_soft, read_matrix)

MAX_VALUES = 200  # a cell-table column with more distinct values is listed without them
MAX_EXPRESSION_CELLS = 30000  # per sample: the expression-based detectors use a random subset of its cells
SUMMARY_MIN_DETECTION = 0.02  # per-guide expression summary: genes detected in at least 2% of the subset's cells
SINGLE_GUIDE_SHARE = 0.30     # the summary uses cells with one called guide when at least this share of cells with
                              # calls has one; otherwise (high MOI) every cell carrying the guide
MAX_SUMMARY = 30_000_000      # guides x genes; larger libraries get no per-guide summary
EXPECTED_CELLS = 3000         # Cell Ranger's expected cells per sample, for its cell call in a matrix of every droplet
UNIT_ARRAYS = {"samples": "samples", "lanes (barcode suffixes of one matrix)": "lanes", "hashtag groups": "hashtags",
               "captures (barcode prefixes of one matrix)": "lanes"}
SCHEMA = "pertura.design/v1"


EVERY_DROPLET_NEXT = ("a matrix of every droplet (Cell Ranger's raw_feature_bc_matrix) with no matrix of its called "
                      "cells beside it is not read as cells: give the filtered matrices Cell Ranger writes beside it "
                      "(filtered_feature_bc_matrix, or cellranger multi's per-sample outputs), or each capture's cells "
                      "called with the field's method (DropletUtils' emptyDrops) and read into cell tables "
                      "(pertura.cell_table.write)")

def _split(a, rng):
    """(gene expression of a random subset of at most MAX_EXPRESSION_CELLS cells, as a copy; guide UMIs of every cell;
    other barcodes of every cell; gene-expression UMIs of every cell). The full matrix can then be freed."""
    ft = a.var["feature_types"].astype(str).to_numpy()
    names = a.var["name"].astype(str).to_numpy()
    X = sp.csr_matrix(a.X)
    G = pd.DataFrame(X[:, np.flatnonzero(ft == GUIDE)].toarray().astype(np.int32), index=a.obs_names,
                     columns=names[ft == GUIDE])
    other = np.flatnonzero((ft != GUIDE) & (ft != "Gene Expression"))
    O = pd.DataFrame(X[:, other].toarray(), index=a.obs_names, columns=names[other])
    expression = np.flatnonzero(ft == "Gene Expression")
    Xg = X[:, expression]
    umis = np.asarray(Xg.sum(1)).ravel()
    n = a.n_obs
    rows = np.sort(rng.choice(n, size=min(n, MAX_EXPRESSION_CELLS), replace=False))
    if len(rows) < n:  # every cell kept: no copy of its rows
        Xg = Xg[rows]
    import anndata as ad
    gex = ad.AnnData(Xg, obs=a.obs.iloc[rows].copy(), var=a.var.iloc[expression].copy())
    return gex, G, O, umis


def _rows(M, r):
    """M[r] for sorted row numbers r: a slice when they are one block (cheaper than picking rows)."""
    return M[r[0]:r[-1] + 1] if len(r) and r[-1] - r[0] + 1 == len(r) else M[r]


def _detected_in_rows(X, r):
    """(X[r] > 0).sum(0) for a CSR matrix and sorted rows r: each column's cells with a count, counted in place when
    the rows are one block."""
    if len(r) and r[-1] - r[0] + 1 == len(r):
        a, b = X.indptr[r[0]], X.indptr[r[-1] + 1]
        idx, dat = X.indices[a:b], X.data[a:b]
        return np.bincount(idx[dat > 0], minlength=X.shape[1])
    return np.asarray((X[r] > 0).sum(0)).ravel()


SMALL_MATRIX, WHOLE_TRANSCRIPTOME = 1000, 10000  # features


def _guide_only(sample, largest=0):
    """A matrix of guides alone, or of few features beside whole-transcriptome matrices (largest: the most features
    any matrix of the deposit has): a separate guide or barcode readout, compared with the expression matrices as a
    guide source rather than read as cells. Its feature names may be the targets' (a guide readout named by gene),
    so names are not used. Returns the numbers that make it one, in words for the reading's note, or None."""
    from .readers import matrix_features
    names, types = matrix_features(sample)
    if types is not None and len(types):
        share = float(np.mean(np.asarray(types) == GUIDE))
        if share >= 0.8:
            return f"{share:.0%} of its {len(names):,} features declared as guides"
    if len(names) < SMALL_MATRIX and largest >= WHOLE_TRANSCRIPTOME:
        return f"{len(names):,} features, beside a matrix of {largest:,}"
    return None


def guide_readouts(samples):
    """The samples that are guide readouts (_guide_only), not cells to read; none where every matrix is one, as
    then no expression matrix stands beside them."""
    from .readers import matrix_features
    largest = max((len(matrix_features(s)[0]) for s in samples), default=0)
    out = [s for s in samples if _guide_only(s, largest)]
    return [] if len(out) == len(samples) else out


def why_guide_readout(sample, samples):
    """The numbers that make a sample of the deposit a guide readout (_guide_only), in words."""
    from .readers import matrix_features
    return _guide_only(sample, max((len(matrix_features(s)[0]) for s in samples), default=0))


def _below(path, root, extracted=()):
    """A path relative to the deposit or to the folder its archives were extracted into."""
    for r in [root, *extracted]:
        r = os.path.abspath(r)
        if os.path.abspath(path).startswith(r + os.sep):
            return os.path.relpath(path, r)
    return os.path.basename(path)


def _free_text(path):
    """A .txt or .md file without a table's consistent separators (a README)."""
    if "readme" in os.path.basename(path).lower():  # documentation by convention, whatever its layout
        return True
    if not path.endswith((".txt", ".md", ".txt.gz")):
        return False
    from .readers import _lines
    head = [x for x in _lines(path, 6) if x]
    if not head:
        return False
    for sep in ("\t", ",", " "):
        counts = {len(x.split()) if sep == " " else x.count(sep) for x in head}
        if len(counts) == 1 and counts.pop() > (1 if sep == " " else 0):
            return False
    return True


def _lanes(barcodes):
    """Barcode suffixes of a combined matrix ('AAAC...-3' -> '3'), or None when there is only one."""
    s = pd.Series(barcodes).str.rsplit("-", n=1).str[1]
    return s.to_numpy() if s.notna().all() and s.nunique() > 1 else None


def hashtag_calls(O, hashtags, sample, workdir=None, lanes=None):
    """Each cell's hashtag by the field's two demultiplexers, Seurat's HTODemux and MULTIseqDemux at their defaults,
    one sample at a time (pertura.hashtags): what each keeps (cells with one hashtag, several or none, per sample and,
    where lanes are given (the lanes or captures of one matrix), per lane, and the samples it could not run on) and
    how far they agree; each hashtag's share of each sample's cells, and of each lane's. The methods mostly differ in
    how many cells they call; the auditor calls the cells with the one it chooses into the cell tables
    (hashtags.write_into_cell_tables), and the design names that column as the units. In the facts on hashtag groups a cell carries the hashtag both methods call it with, or,
    in a sample only one of them could run on, that one's. Returns (each cell's hashtag or None, facts, questions)."""
    from .. import hashtags as ht
    cols = [h for h in hashtags if h in O.columns]
    H = sp.csr_matrix(O[cols].to_numpy(float))
    sample = np.asarray(sample).astype(str)
    try:
        by = ht.demultiplex_each(H, cols, groups=sample, workdir=workdir)
    except Exception as e:  # R or Seurat missing or failing as a whole: said, never hidden
        return None, [dt.fact("units.hashtag_calls_by_method",
                              f"not measured ({str(e).splitlines()[0][:300]})", "observed", "Seurat")], [
            dt.question("units.unit_column", "Pertura could not run Seurat's demultiplexers on the hashtag "
                        "counts. Call each cell's hashtag with your own code (run_code), write it into the cell "
                        "tables (pertura.cell_table.annotate) and name the column in units.unit_column.",
                        "the hashtags tell the units apart")]
    lanes = None if lanes is None else np.asarray(lanes).astype(str)

    def by_group(where, call):
        return {s: {k: int(((where == s) & (call == k)).sum()) for k in ("one", "several", "none")}
                for s in pd.unique(where)}
    value = {}
    for method, (_, call, info) in by.items():
        value[method] = {"cells": info["calls"], "samples_not_run": info["groups_not_called"],
                         "by_sample": by_group(sample, call)}
        if lanes is not None:
            value[method]["by_lane"] = by_group(lanes, call)
    t1, t2 = by["htodemux"][0], by["multiseqdemux"][0]
    both = (t1 != "") & (t2 != "")
    value["cells_both_call_with_one_hashtag"] = int(both.sum())
    value["same_hashtag_where_both_call_one"] = round(float(np.mean(t1[both] == t2[both])), 3) if both.any() else None
    facts = [dt.fact("units.hashtag_calls_by_method", value, "observed",
                     "Seurat's HTODemux and MULTIseqDemux at their defaults, one sample at a time",
                     "the methods mostly differ in how many cells they call (one hashtag, several, none); the cells "
                     "are called into the cell tables by the method the auditor chooses")]
    qs = [dt.question("units.unit_column", "Which demultiplexer, with which settings, should call each cell's "
                      "hashtag? The measurement gives what HTODemux and MULTIseqDemux keep in each sample and where "
                      "they could not run; draw the hashtag counts where they differ and try settings if needed. Then "
                      "write each cell's hashtag into the cell tables (pertura.hashtags.write_into_cell_tables) and "
                      "name the column in units.unit_column, with the method and the reason in its source.",
                      "cells without one clear hashtag have no unit: the method sets how many cells each unit keeps")]
    failed1 = np.isin(sample, list(by["htodemux"][2]["groups_not_called"]))
    failed2 = np.isin(sample, list(by["multiseqdemux"][2]["groups_not_called"]))
    tag = np.where(failed1 & ~failed2, t2, np.where(failed2 & ~failed1, t1, np.where(both & (t1 == t2), t1, "")))
    tag = tag.astype(object)
    if lanes is not None:
        by_lane = ht.shares_by_sample(tag, lanes)
        if len(by_lane) >= 2:
            facts.append(dt.fact("units.hashtag_shares_by_lane", by_lane, "observed",
                                 "the cells both methods call with the same hashtag (one method's, where only one ran)",
                                 "each hashtag's share of a lane's cells (the lanes or captures of one matrix); one "
                                 "pool split over lanes gives each hashtag the same share in every lane"))
    shares = ht.shares_by_sample(tag, sample)
    if len(shares) >= 2:
        facts.append(dt.fact("units.hashtag_shares_by_sample", shares, "observed",
                             "the cells both methods call with the same hashtag (one method's, where only one ran)",
                             "each hashtag's share of a sample's cells; one pool split over lanes gives each hashtag "
                             "the same share in every sample"))
        seen_in = {}
        for s, row in shares.items():
            for h, x in row.items():
                if x > 0:
                    seen_in.setdefault(h, set()).add(s)
        if any(len(v) >= 2 for v in seen_in.values()):
            qs.append(dt.question("units.unit_column", "Some hashtags appear in several samples. "
                                  "Does one hashtag mark the same cells in every sample (one pool split over lanes "
                                  "or captures): one animal, one donor or one condition's cells? Or are hashtag "
                                  "names reused for other animals, donors or conditions in other samples? Say which "
                                  "when the hashtags are written into the cell tables (across_samples of "
                                  "pertura.hashtags.write_into_cell_tables).",
                                  "the same animal, donor or condition counted once per sample multiplies its "
                                  "weight in every test across units"))
    return tag, facts, qs


def guide_sets(calls, cells, guides):
    """CSR (cells x guides): 1 for each guide called in each cell."""
    gi = {g: j for j, g in enumerate(guides)}
    pos = {bc: i for i, bc in enumerate(cells)}
    rows, cols = [], []
    for bc, v in calls.items():
        for g in {g for g, _ in v}:
            if g in gi:
                rows.append(pos[bc])
                cols.append(gi[g])
    return sp.csr_matrix((np.ones(len(rows), np.float32), (rows, cols)), shape=(len(cells), len(guides)))


def guide_expression(X, genes, A, guides, chunk=20000):
    """Per guide, over the expression subset's cells that carry it (A: subset cells x guides): the summed CP10k and the
    number of cells detecting each gene, for genes detected in at least SUMMARY_MIN_DETECTION of the subset's cells.
    None when guides x genes exceeds MAX_SUMMARY."""
    X = sp.csr_matrix(X)
    det = np.asarray((X > 0).sum(0)).ravel()
    keep = np.flatnonzero(det >= SUMMARY_MIN_DETECTION * max(X.shape[0], 1))
    if A.shape[1] * len(keep) > MAX_SUMMARY:
        return None
    S = np.zeros((A.shape[1], len(keep)), np.float64)
    D = np.zeros((A.shape[1], len(keep)), np.int64)
    lib = np.asarray(X.sum(1)).ravel()
    for r in range(0, X.shape[0], chunk):
        Xc = X[r:r + chunk][:, keep]
        w = 1e4 / np.maximum(lib[r:r + chunk], 1)
        # the chunk in CP10k and its detected entries, built on its own structure: each entry w[cell] * count, as
        # diags(w) @ Xc gives it, without the intermediate copies
        scaled = sp.csr_matrix((np.repeat(w, np.diff(Xc.indptr)) * Xc.data, Xc.indices, Xc.indptr), shape=Xc.shape)
        detected = sp.csr_matrix(((Xc.data > 0).astype(np.float32), Xc.indices, Xc.indptr), shape=Xc.shape)
        Ac = sp.csr_matrix(A[r:r + chunk]).T.tocsr()
        S += np.asarray((Ac @ scaled).todense())
        D += np.asarray((Ac @ detected).todense()).astype(np.int64)
    return {"guides": np.asarray(guides, dtype=str), "genes": np.asarray(genes, dtype=str)[keep],
            "sum": S.astype(np.float32), "detected": D.astype(np.int32),
            "cells": np.asarray(A.sum(0)).ravel().astype(np.int32)}


def own_genes(X, genes, rows, guides, target_of, most=MAX_SUMMARY):
    """Each target's own gene per cell of the expression subset, in CP10k: the genes the matrix holds that the feature
    reference names as targets, or that begin a guide's name before its last '_', '-' or '.' (no target map is known
    yet; a check reads only the proposed map's targets among them), with the subset's rows among all cells. A proposed
    calling rule's cells are then read on their own genes (checks._assignment_left). None where cells x genes exceeds
    most, as the per-guide summary."""
    names = np.asarray(genes).astype(str)
    named = {str(t) for t in target_of.values()} | {str(g) for g in guides}
    named |= {str(g).rsplit(sep, 1)[0] for g in guides for sep in "_-." if sep in str(g)}
    cols = {}
    for j, g in enumerate(names):  # a gene held twice reads its first column
        if g in named:
            cols.setdefault(g, j)
    if not cols or X.shape[0] * len(cols) > most:
        return None
    X = sp.csr_matrix(X)
    lib = np.asarray(X.sum(1)).ravel()
    sub = sp.csr_matrix(sp.diags(1e4 / np.maximum(lib, 1)) @ X[:, list(cols.values())], dtype=np.float32)
    return {"own_rows": np.asarray(rows, dtype=np.int64), "own_genes": np.asarray(list(cols), dtype=str),
            "own_indptr": sub.indptr.astype(np.int64), "own_indices": sub.indices.astype(np.int32),
            "own_data": sub.data.astype(np.float32)}


def quality_fact(X, genes, samples):
    """data.quality: per sample, the cells' UMIs, genes detected and mitochondrial and ribosomal shares (5th
    percentile, median, 95th), and the share of cells past common mitochondrial thresholds, on the expression subset;
    its words say how many genes each share counts (pertura.quality.mito_ribo)."""
    from ..quality import genes_counted, mito_ribo, none_counted
    mito, ribo = mito_ribo(genes)
    lib = np.maximum(np.asarray(X.sum(1)).ravel(), 1)
    measures = {"n_counts": np.asarray(X.sum(1)).ravel().astype(float),
                "n_genes": np.asarray((X > 0).sum(1)).ravel().astype(float),
                "pct_mito": np.asarray(X[:, np.flatnonzero(mito)].sum(1)).ravel() / lib,
                "pct_ribo": np.asarray(X[:, np.flatnonzero(ribo)].sum(1)).ravel() / lib}
    per = {}
    for s in sorted(set(map(str, samples))):
        m = np.asarray(samples).astype(str) == s
        row = {"cells": int(m.sum())}
        for k, v in measures.items():
            q = np.percentile(v[m], [5, 50, 95]) if m.any() else [np.nan] * 3
            row[k] = {"p5": round(float(q[0]), 4), "median": round(float(q[1]), 4), "p95": round(float(q[2]), 4)}
        row["pct_mito_above"] = {str(t): round(float(np.mean(measures["pct_mito"][m] > t)), 3) for t in (0.05, 0.1, 0.2)}
        per[s] = row
    counted = genes_counted(genes)
    none = none_counted(counted)
    return dt.fact("data.quality", per, "observed", "matrices",
                   f"the cells' UMIs, genes and mitochondrial (genes named mt-: {counted['pct_mito']['genes']}) and "
                   f"ribosomal (named rps or rpl: {counted['pct_ribo']['genes']}) shares per sample, for the quality "
                   "rule (data.qc); nothing is left out by measuring" + (f". {none}" if none else ""))


def likely_cells(total_umis, sample, listed=None, expected=EXPECTED_CELLS):
    """Which barcodes of a matrix of every droplet are cells, and the words for it: those the deposit's call files
    list, where they list any; else, in each sample (one Cell Ranger run), Cell Ranger's own cell call before its
    EmptyDrops step (Zheng et al. 2017): the barcodes with a tenth or more of the 99th percentile of the sample's
    `expected` barcodes with most UMIs."""
    if listed is not None and np.any(listed):
        return np.asarray(listed, bool), "the cells the deposit's call files list"
    umis, sample = np.asarray(total_umis, dtype=np.float64), np.asarray(sample)
    out = np.zeros(len(umis), bool)
    for s in pd.unique(sample):
        idx = np.flatnonzero(sample == s)
        top = np.sort(umis[idx])[::-1][:expected]
        line = np.percentile(top, 99) / 10 if len(top) else 0
        out[idx] = (umis[idx] >= line) & (umis[idx] > 0)
    return out, (f"Cell Ranger's cell call: in each sample, barcodes with a tenth or more of the 99th percentile of "
                 f"its {expected:,} barcodes with most UMIs")


def ambient_guide_share(guide_umis, cells, sample, raw):
    """The share of the guide UMIs in barcodes that are not cells, in all and per sample (docs/adr/0037, the D4
    follow-up: in one deposit's library about 80% lay outside the cells). Where it is high, guides float free in the
    droplets, and a rule that calls a guide on any UMI calls ambient ones. Measured only in a matrix of every droplet;
    a matrix of called cells holds none of the rest."""
    if not raw:
        return dt.fact("perturbation.ambient_guide_share", "not measured: the matrices hold the called cells only",
                       "observed", "guide UMIs per barcode")
    u, cells, sample = np.asarray(guide_umis, dtype=np.float64), np.asarray(cells, bool), np.asarray(sample)

    def share(m):
        total = u[m].sum()
        return round(float(u[m & ~cells].sum() / total), 3) if total else None
    return dt.fact("perturbation.ambient_guide_share",
                   {"outside_cells": share(np.ones(len(u), bool)),
                    "by_sample": {str(s): share(sample == s) for s in pd.unique(sample)}},
                   "observed", "guide UMIs per barcode",
                   "the share of guide UMIs in barcodes that are not cells: a guide called on one or two UMIs may "
                   "be ambient where it is high")


def audit(root, geo_soft=None, extract_to=None, parsed=None, workers=1, extract=True, cell_tables=None):
    """root: the deposit's folder. extract_to: a folder where its archives may be extracted (each into its own
    folder); without one, archives are listed with a request to extract them; with extract False, only those
    extracted there before are read. parsed: a folder for the parsed matrices (pertura.design_audit.readers), filled
    by `workers` processes at once. cell_tables: the project's cell-table folder; a deposit read into cell tables
    is measured from them (pertura.cell_table)."""
    dep, extracted, beside = open_deposit(root, extract_to, extract, cell_tables)
    all_files = sorted(set(_files(root) + [f for e in extracted for f in _files(e, depth=4)]))
    facts = [dt.fact("data.layout", dep["layout"], "observed", "file names",
                     f"{len(dep['samples'])} matrices, {len(dep['calls'])} call files, "
                     f"{len(dep['feature_references'])} feature references, {len(dep['objects'])} objects, "
                     f"{len(dep['archives'])} archives")]
    if extracted:
        facts.append(dt.fact("data.archives_extracted", [os.path.basename(e) for e in extracted], "observed",
                             "archives", f"extracted to {extract_to}, each into its own folder"))
    if beside:
        facts.append(dt.fact("data.archives_already_extracted", [os.path.basename(a) for a in beside], "observed",
                             "archive contents against the deposit's files", "their files already sit in the "
                             "deposit; not extracted again"))
    if dep.get("duplicates"):
        facts.append(dt.fact("data.duplicate_matrices", dep["duplicates"], "observed", "barcodes",
                             "the same cells in two formats; read once"))
    if dep["layout"] == "cell_tables":
        facts.append(dt.fact("data.read_by", {s["name"]: s.get("made_by", "") for s in dep["samples"]}, "observed",
                             "the cell tables' manifest", "the deposit was read into cell tables; the reader of each "
                             "sample (a Pertura reader, or the agent's run)"))
    if dep.get("every_droplet"):
        facts.append(dt.fact("data.every_droplet",
                             [{"matrix": os.path.basename(e["matrix"].rstrip("/\\")) or e["matrix"],
                               "barcodes_in_one_lane": e["barcodes_in_one_lane"]} for e in dep["every_droplet"]],
                             "observed", "barcodes", EVERY_DROPLET_NEXT))
    if dep.get("unreadable"):
        facts.append(dt.fact("data.unreadable", [{"file": os.path.basename(u["file"]), "error": u["error"]}
                                                 for u in dep["unreadable"]], "observed", "files",
                             "matrices Pertura's readers could not open; not read"))
    qs = []
    geo_path = geo_soft or (dep["geo_soft"][0] if dep["geo_soft"] else None)
    geo = read_geo_soft(geo_path) if geo_path and os.path.exists(geo_path) else None
    ref = read_feature_reference(dep["feature_references"][0]) if dep["feature_references"] else None
    free_text = [f for f in all_files if _free_text(f)]
    if not dep["samples"]:
        if dep["objects"]:
            return _audit_object(dep, ref, facts, qs, geo, all_files, free_text)
        if dep["archives"]:
            text = ("The deposit holds archives (" + ", ".join(os.path.basename(a) for a in dep["archives"])
                    + "). Extract them, or point to the matrices.")
        elif dep.get("every_droplet"):
            text = f"Each matrix holds every droplet: {EVERY_DROPLET_NEXT}."
        else:
            bad = dep.get("unreadable") or []
            text = ("Pertura's readers found no matrix they can read"
                    + (f" ({len(bad)} could not be opened: "
                       + ", ".join(os.path.basename(u["file"]) for u in bad[:5]) + ")" if bad else "")
                    + ". Read the deposit into cell tables with your own code (run_code, with "
                      "pertura.cell_table.write), or point to the matrices.")
        qs.append(dt.question("data.files", text, "nothing can be read until then"))
        f, q = inv.samples_inventory(all_files, geo, has_guide_data=True)
        return _result(dep, facts + f, qs + q)
    import gc

    import anndata as ad
    rng = np.random.default_rng(0)
    declared = declared_types(ref)
    guide_only = guide_readouts(dep["samples"])
    expression_samples = [s for s in dep["samples"] if s not in guide_only]
    if parsed:
        parse_matrices(dep["samples"], declared, parsed, workers)
    gex_parts, G_parts, O_parts, sample, sample_expr, tables, vars_ = [], [], [], [], [], {}, []
    umis_by_sample, conflicts, umi_parts, barcodes_by_sample = {}, [], [], {}
    table_values = {}  # the cell tables' columns: their values (pertura.cell_table), for units and conditions
    table_counts = {}  # and their values' cells in each sample, for the form
    for s in expression_samples:
        a = read_matrix(s, declared, parsed)
        if s["kind"] == "cell_table":
            for c in a.obs.columns:
                counts = a.obs[c].astype(str).value_counts().head(MAX_VALUES + 1)
                table_values.setdefault(str(c), set()).update(counts.index.tolist())
                table_counts.setdefault(str(c), {})[s["name"]] = {str(k): int(n) for k, n in counts.items()}
        conflicts += a.uns.get("type_conflicts", [])
        barcodes_by_sample[s["name"]] = set(map(str, a.obs_names))
        a.obs_names = [f"{s['name']}:{b}" for b in a.obs_names]
        vars_.append(a.var.copy())
        g, G, O, umis = _split(a, rng)
        umis_by_sample[s["name"]] = umis
        umi_parts.append(umis)
        gex_parts.append(g)
        G_parts.append(G)
        O_parts.append(O)
        sample += [s["name"]] * a.n_obs
        sample_expr += [s["name"]] * g.n_obs
        if s.get("calls"):
            tables[s["name"]] = read_calls(s["calls"])
        del a
        gc.collect()
    f, q = inv.raw_droplets(umis_by_sample)
    facts += f
    qs += q
    f, q = inv.same_cells(barcodes_by_sample)
    facts += f
    qs += q
    f, q = inv.type_conflicts(conflicts)
    facts += f
    qs += q
    # every matrix's features: lanes of one deposit can carry different hashtags (one set per condition)
    var = pd.concat(vars_).drop_duplicates(subset=["name", "feature_types"])
    gex = ad.concat(gex_parts, join="inner") if len(gex_parts) > 1 else gex_parts[0]
    del gex_parts
    X = sp.csr_matrix(gex.X)  # expression of the subset; every detector below shares this one matrix
    genes = np.asarray(gex.var_names)
    G = pd.concat(G_parts).fillna(0).astype(np.int32)
    O = pd.concat(O_parts).fillna(0)
    total_umis = np.concatenate(umi_parts)
    del G_parts, O_parts
    cells = np.asarray(G.index)  # every cell of every sample
    sample, sample_expr = np.asarray(sample), np.asarray(sample_expr)
    facts.append(dt.fact("data.cells", {"all": int(len(cells)), "expression_subset": int(X.shape[0])}, "observed",
                         "matrices", f"expression-based detectors use up to {MAX_EXPRESSION_CELLS} random cells per "
                                     "sample"))
    facts.append(quality_fact(X, genes, sample_expr))
    f, q, hashtags = dt.feature_classes(var, O)
    facts += f
    qs += q
    gex_var = var[var["feature_types"].astype(str) == "Gene Expression"]
    ref_names = set(ref["name"].astype(str)) if ref is not None and "name" in ref else set()
    id_of, name_of = {}, {}
    for v in vars_:  # var_names are the unique names of each matrix; their IDs and plain names
        for un, gid, nm in zip(v.index.astype(str), v["gene_ids"].astype(str) if "gene_ids" in v else v.index,
                               v["name"].astype(str) if "name" in v else v.index):
            id_of.setdefault(un, str(gid))
            name_of.setdefault(un, str(nm))
    f, q = inv.unannotated_features(np.array([id_of.get(g, g) for g in genes]),
                                    np.array([name_of.get(g, g) for g in genes]), X, ref_names)
    facts += f
    qs += q
    guide_ids = (var.loc[var["feature_types"].astype(str) == GUIDE, "gene_ids"].astype(str).tolist()
                 if "gene_ids" in var else None)
    f, q = dt.guide_names(list(G.columns), list(genes), next(iter(tables.values()), None), guide_ids)
    facts += f
    qs += q
    f, q, target_of = dt.library(list(G.columns), ref)
    facts += f
    qs += q
    calls = {}
    raw_droplets = any(x["key"] == "data.raw_droplets" for x in facts)  # a matrix of every droplet: which are cells
    if tables:
        for name, t in tables.items():
            for bc, v in dt.parse_calls(t, list(G.columns), guide_ids).items():
                calls[f"{name}:{bc}"] = v
        status, source = "observed", "call files"
    else:  # the build's default without call files: Cell Ranger's own method, one sample (one run) at a time
        from ..screen_object import cellranger_thresholds
        M = G.to_numpy()
        seen = [[(G.columns[j], int(M[i, j])) for j in np.flatnonzero(M[i] > 0)] for i in range(len(cells))]
        among = likely_cells(total_umis, sample)[0] if raw_droplets else np.ones(len(cells), bool)
        status, source = "inferred", ("Cell Ranger's own method on the guide UMIs, one sample at a time (no call "
                                      "files), as the build calls them by default")
        if G.shape[1]:
            idx = np.flatnonzero(among)
            try:
                thr = cellranger_thresholds([seen[i] for i in idx], sample[idx], 1, cache=parsed)
            except Exception as e:  # scikit-learn's mixture failing in this environment: said, never hidden
                thr = {}
                source = f"not measured: Cell Ranger's method could not run here ({str(e).splitlines()[0][:200]})"
            for i in idx:
                hit = [(g, u) for g, u in seen[i] if thr.get((sample[i], g)) is not None and u >= thr[(sample[i], g)]]
                if hit:
                    calls[cells[i]] = hit
        if G.shape[1]:
            qs.append(dt.question("perturbation.assignment", "No guide calls are deposited. Which rule assigned "
                                  "guides to cells (the authors')?", "a fallback threshold may call ambient guides"))
    in_matrix = set(cells)
    calls = {bc: v for bc, v in calls.items() if bc in in_matrix}
    facts.append(dt.fact("perturbation.assignment_source", source, status, source))
    pert = np.full(len(cells), "", dtype=object)
    pos = {bc: i for i, bc in enumerate(cells)}
    for bc, v in calls.items():
        ts = {target_of.get(g, g) for g, _ in v}
        if len(ts) == 1:
            pert[pos[bc]] = ts.pop()
    if calls or G.shape[1]:
        f, q = dt.moi(calls, target_of, len(cells) - len(calls))
        facts += f
        qs += q
    lanes = _lanes([b.split(":", 1)[1] for b in cells]) if len(expression_samples) == 1 else None
    if lanes is not None:  # named as the build names them, so a proposal's keys are the screen's units
        lanes = np.array([f"{s}-{x}" for s, x in zip(sample, lanes)], dtype=object)
    prefixes = sorted({c["prefix"] for s in expression_samples if isinstance(s.get("calls"), list)
                       for c in s["calls"] if c.get("prefix")}, key=len, reverse=True)
    if len(expression_samples) > 1:
        unit, label = sample, "samples"
    elif len(prefixes) > 1:  # one matrix of several captures, told apart by the barcode prefixes its calls need
        raw = [b.split(":", 1)[1] for b in cells]
        # named as the build names them (the prefix without its trailing separator)
        unit = np.array([next((p.rstrip("_:-") for p in prefixes if b.startswith(p)), "") for b in raw], dtype=object)
        label = "captures (barcode prefixes of one matrix)"
    elif lanes is not None:
        unit, label = lanes, "lanes (barcode suffixes of one matrix)"
    else:
        unit, label = sample, "samples"
    unit = np.asarray(unit, dtype=str)
    f, q, _ = dt.units(unit, pert, label)
    facts += f
    qs += q
    if G.shape[1]:
        f, q = dt.guide_capture_by_unit(G, unit)
        facts += f
        qs += q
    # cell metadata: tables keyed by the matrix's barcodes
    raw_bcs = np.array([b.split(":", 1)[1] for b in cells])
    meta, meta_guides, meta_labels = None, [], []
    for tpath in dep["tables"]:
        if os.path.getsize(tpath) > 500_000_000 or _free_text(tpath):
            continue
        try:
            table = pd.read_csv(tpath, sep=None, engine="python")
        except Exception:
            continue
        f, q, t, gcols, lcols = inv.cell_metadata(table, raw_bcs, list(G.columns) + list(ref_names))
        if t is not None:
            facts += f
            qs += q
            meta, meta_guides, meta_labels = t, gcols, lcols
            facts += inv.columns_by_unit(t, raw_bcs, unit)  # what a fact read from a column is checked against
            break
    has_guides = bool(G.shape[1] or calls or meta_guides)
    likely, likely_words = np.ones(len(cells), bool), None
    if raw_droplets:  # guide statistics over the cells only: the deposit's, else Cell Ranger's call
        likely, likely_words = likely_cells(total_umis, sample, np.isin(cells, list(calls)) if tables else None)
    if G.shape[1]:  # beside the calling rule, for the form
        facts.append(ambient_guide_share(G.sum(1).to_numpy(), likely, sample, raw_droplets))
    if calls:
        cl = {bc: v for bc, v in calls.items() if likely[pos[bc]]}
        f, q = inv.guide_reliability(cells[likely], unit[likely],
                                     G.sum(1).to_numpy()[likely] if G.shape[1] else None, cl, target_of,
                                     combinatorial_share=0.5)
        if not likely.all():
            for x in f:
                x["note"] = ((x.get("note") or "") + f" ({likely_words})").strip()
        facts += f
        qs += q
    rows = np.array([pos[c] for c in gex.obs_names])  # the expression subset's cells among all cells
    unit_x = unit[rows]
    if len(expression_samples) > 1:
        detected = {}
        for s in pd.unique(sample_expr):
            r = np.flatnonzero(sample_expr == s)
            detected[s] = int((_detected_in_rows(X, r) >= 0.01 * len(r)).sum())
        f, q = dt.readouts(detected)
        facts += f
        qs += q
    unit_arrays = {UNIT_ARRAYS[label]: unit}
    tag = None
    if hashtags:
        tag, f, q = hashtag_calls(O, hashtags, sample, workdir=parsed,
                                  lanes=unit if UNIT_ARRAYS[label] == "lanes" else None)
        facts += f
        qs += q
    if tag is not None:
        unit_arrays["hashtags"] = np.asarray(tag, dtype=str)
        f, _, _ = dt.units(tag, pert, "hashtag groups")
        facts += [dict(x, key=x["key"].replace("units.", "units.by_hashtag.", 1)) for x in f]
        f, q = inv.hashtags_within_units(unit, tag, label)
        facts += f
        qs += q
    f, q = dt.pooled_donors(X, genes, unit_x)
    facts += f
    qs += q
    lib = np.asarray(X.sum(1)).ravel()
    top_genes = np.argsort(-np.asarray(X.sum(0)).ravel())[:2000]
    Xt = X[:, top_genes]
    prof = pd.DataFrame({u: np.asarray(_rows(Xt, np.flatnonzero(unit_x == u)).sum(0)).ravel()
                         / max(lib[unit_x == u].sum(), 1) * 1e6 for u in pd.unique(unit_x)}).T
    f, q = dt.conditions(geo, prof if len(prof) >= 4 else None)
    facts += f
    qs += q
    guides = [str(g) for g in G.columns]
    guides += sorted({g for v in calls.values() for g, _ in v} - set(guides))  # guides known only from the calls
    A = guide_sets(calls, cells, guides)  # every cell's called guides
    if calls:
        f, q = inv.cooccurrence(A, guides, target_of, total_umis, set(genes) | set(name_of.values()))
        facts += f
        qs += q
        by_sample = pd.DataFrame({s: np.asarray(A[np.flatnonzero(sample == s)].sum(0)).ravel()
                                  for s in pd.unique(sample)}, index=guides).T
        f, q = inv.guides_by_sample(by_sample)
        facts += f
        qs += q
        single = (A.getnnz(axis=1) == 1) & likely
        one = pd.Series(np.asarray(guides)[A[single].indices], dtype=str)
        per_unit = pd.crosstab(pd.Series(unit[single], dtype=str), one) if len(one) else pd.DataFrame()
        if not per_unit.empty:
            animals, alabel = per_unit, label
            if tag is not None and any(x["key"] == "units.hashtags_within" for x in facts):
                # lanes holding several hashtag groups are not animals: lane x hashtag groups are
                lh = pd.Series([f"{u}:{t}" if t else "" for u, t in zip(unit, tag)], dtype=str)
                keep = single & (lh.to_numpy() != "")
                animals = pd.crosstab(lh[keep], pd.Series(np.asarray(guides)[A[keep].indices], dtype=str))
                alabel = "lane and hashtag groups"
            f, q = inv.unit_similarity(animals, alabel)
            facts += f
            qs += q
            f, q = inv.unit_specific_guides(per_unit, label)
            facts += f
            qs += q
            ctrl = [g for g in per_unit.columns if target_of.get(g) == "control"]
            f, q = inv.control_spread(per_unit[ctrl], label)
            facts += f
            qs += q
    # labels per cell (the deposit's own, else targets from the calls) for pairs; factors per unit
    labels = None  # the deposit's own labels: cells with several ambient guides are not a combinatorial library
    if meta is not None and meta_labels:
        labels = meta.reindex(raw_bcs)[meta_labels[0]].fillna("").astype(str).to_numpy()
    if labels is not None:
        f, q = inv.testable_pairs(labels, unit, label)
        facts += f
        qs += q
    if meta is not None and meta_guides and ref is not None:
        f, q = inv.label_disagreements(dt.reference_labels(ref), meta, meta_guides[0])
        facts += f
        qs += q
    for s in expression_samples:
        if isinstance(s.get("calls"), list) and any(c.get("suffix") for c in s["calls"]):
            for tpath in dep["tables"]:
                try:
                    t = pd.read_csv(tpath, sep=None, engine="python")
                except Exception:
                    continue
                if len(t) == len({c.get("suffix") for c in s["calls"]}):
                    f, q = inv.aggregation_order(t, t.columns[0], s["calls"])
                    facts += f
                    qs += q
    uf = pd.DataFrame(index=pd.unique(unit))
    if calls:
        share_pairs = pd.Series([len({target_of.get(g, g) for g, _ in calls[bc]}) >= 2 if bc in calls else False
                                 for bc in cells]).groupby(unit).mean()
        uf["cells with two or more targets"] = np.where(  # a unit-level factor for the confounding question
            share_pairs.reindex(uf.index) >= 0.5, "half or more",
            np.where(share_pairs.reindex(uf.index) <= 0.1, "a tenth or fewer", "between a tenth and half"))
    if meta is not None:
        m = meta.reindex(raw_bcs)
        for c in m.columns:
            if m[c].dtype.kind in "fiu" or c in meta_guides or c in meta_labels:
                continue
            per = m[c].astype(str).groupby(unit).agg(lambda v: v.value_counts(normalize=True).iloc[0])
            if (per >= 0.95).all() and m[c].nunique() <= 24:
                uf[str(c)] = m[c].astype(str).groupby(unit).agg(most_common_value)
    f, q = inv.factor_confounding(uf)
    facts += f
    qs += q
    sources = {}
    if G.shape[1] and guide_only:  # guides inside the expression files, as a source of their own
        has = G.sum(1).to_numpy() > 0
        for s in expression_samples:
            inside = has & (sample == s["name"])
            if inside.any():
                sources[f"guide features inside {s['name']}"] = {
                    "barcodes": {b.split(":", 1)[1] for b in cells[inside]}, "assigned": None}
    for s in guide_only:
        from .readers import read_matrix as _rm
        try:
            bcs = set(map(str, _rm(s, declared, parsed).obs_names))
        except Exception:
            continue
        sources[s["name"]] = {"barcodes": bcs, "assigned": None}
    if sources:
        f, q = inv.guide_sources(sources, barcodes_by_sample)
        facts += f
        qs += q
    guide_like = any(x["key"] == "data.unannotated_features" and "as guides do" in (x.get("note") or "")
                     for x in facts)
    named = [inv.GSM.findall(_below(s["matrix"], root, extracted)) for s in expression_samples]
    f, q = inv.characteristic_per_matrix(geo, len(expression_samples),
                                         {g for n in named for g in n} if all(named) else None)
    facts += f
    qs += q
    f, q = inv.samples_differ(geo, {g for n in named for g in n} if all(named) else None)
    facts += f
    qs += q
    like_names = next((x["value"].get("all_names", []) for x in facts if x["key"] == "data.unannotated_features"), [])
    f, q = inv.library_against_metadata(guides or like_names, set(genes) | set(name_of.values()), geo)
    facts += f
    qs += q
    f, q = inv.samples_inventory([_below(p, root, extracted) for p in all_files], geo,
                                 has_guides or bool(sources) or guide_like)
    facts += f
    qs += q
    annotation_cols = [c for c in (meta.columns if meta is not None else [])
                       if meta[c].dtype.kind not in "fiu" and 3 <= meta[c].nunique() <= 200
                       and c not in meta_guides and c not in meta_labels and c not in uf.columns]
    qs += inv.reading_questions(free_text, annotation_cols, bool(hashtags))
    Ax = A[rows]
    n_called = Ax.getnnz(axis=1)
    single = n_called == 1
    if single.sum() >= SINGLE_GUIDE_SHARE * max((n_called > 0).sum(), 1):
        # cells with one called guide: ambient calls would otherwise dilute each guide's own effect
        Ax, basis = sp.diags(single.astype(np.float32)) @ Ax, "cells with one called guide"
    else:
        basis = "cells carrying the guide (most cells carry several)"
    expr = guide_expression(X, genes, Ax, guides)
    if expr is not None:
        expr["basis"] = np.asarray(basis)
    f, q = dt.modality(expr, target_of, genes)
    facts += f
    qs += q
    per_guide = pd.Series([g for v in calls.values() for g in {g for g, _ in v}], dtype=str).value_counts()
    library = {"guides": guides, "cells_per_guide": {str(k): int(v) for k, v in per_guide.items()},
               "target_of": {str(k): str(v) for k, v in target_of.items()},
               "reference_labels": {g: t for g, t in dt.reference_labels(ref).items() if g in set(guides)},
               "targets_source": next((x["status"] for x in facts if x["key"] == "perturbation.targets"), None),
               "samples": [s["name"] for s in expression_samples], "hashtags": [str(h) for h in hashtags],
               "object_columns": [], "units": sorted(unit_arrays), "genes": [str(g) for g in genes],
               "cell_table_columns": {c: (sorted(v) if len(v) <= MAX_VALUES else None)
                                      for c, v in sorted(table_values.items())},
               "cell_table_columns_by_sample": {c: table_counts[c] for c, v in sorted(table_values.items())
                                                if len(v) <= MAX_VALUES}}
    # each cell's guide UMIs, in the order of guides (one known only from the calls holds none in the matrix): what a
    # proposed calling rule leaves is counted on them before any build (checks._assignment_left)
    U = sp.csr_matrix(G.to_numpy())
    if len(guides) > U.shape[1]:
        U = sp.hstack([U, sp.csr_matrix((U.shape[0], len(guides) - U.shape[1]), dtype=U.dtype)]).tocsr()
    arrays = {"guides": np.asarray(guides, dtype=str), "calls_indptr": A.indptr.astype(np.int64),
              "calls_indices": A.indices.astype(np.int32), "umis_indptr": U.indptr.astype(np.int64),
              "umis_indices": U.indices.astype(np.int32), "umis_data": U.data.astype(np.int32)}
    for k, v in {**{f"unit_{u}": x for u, x in unit_arrays.items()}, "cell_samples": sample}.items():
        codes, names = pd.factorize(pd.Series(v, dtype=str))  # cell_samples: each cell's run, as the fits read them
        arrays[f"{k}_codes"], arrays[f"{k}_names"] = codes.astype(np.int32), np.asarray(names, dtype=str)
    if expr is not None:
        arrays.update({f"expr_{k}": v for k, v in expr.items() if k != "guides"})
    arrays.update(own_genes(X, genes, rows, guides, target_of) or {})
    return _result(dep, facts, qs, library, arrays)


def _audit_object(dep, ref, facts, qs, geo, all_files=(), free_text=()):
    """A processed object: its cell metadata, against the deposit's guide list (feature reference)."""
    import anndata as ad
    a = ad.read_h5ad(dep["objects"][0], backed="r")
    obs = a.obs
    facts.append(dt.fact("data.object", {"file": os.path.basename(dep["objects"][0]), "cells": int(a.n_obs),
                                         "columns": [str(c) for c in obs.columns]}, "observed", "h5ad"))
    n = min(a.n_obs, 2000)
    block = a.X[:n]
    block = block.toarray() if sp.issparse(block) else np.asarray(block)
    stored = {str(c): obs[c].to_numpy()[:n] for c in obs.columns if obs[c].dtype.kind in "fiu"}
    f, q = inv.matrix_content(block, stored, marked_log="log1p" in a.uns)
    facts += f
    qs += q
    guides = ref["name"].astype(str).tolist() if ref is not None and "name" in ref else []
    target_of, guide_col = {}, None
    if guides:
        f, q = dt.object_columns(obs, guides)
        facts += f
        qs += q
        f, q, target_of = dt.library(guides, ref)
        facts += f
        qs += q
        known = set(guides)
        best = 0.0
        for c in obs.columns:  # the guide column with the most cells holding exactly one guide
            share = float(obs[c].astype(str).isin(known).mean())
            if share > best:
                guide_col, best = c, share
    else:
        qs.append(dt.question("perturbation.library", "No guide list (feature reference) is deposited with the "
                              "object. Which guides and controls does the library hold?", "every check needs it"))
    candidates, labels_from_guides, channel_level = [], [], []
    if guide_col is not None:
        g = obs[guide_col].astype(str)
        one = g.isin(set(guides))
        f, q = inv.label_disagreements(dt.reference_labels(ref), obs[one], guide_col)
        facts += f
        qs += q
        labels_from_guides = [c for c in obs.columns if c != guide_col and obs[c].dtype.kind not in "fiu"
                              and inv._derived(obs[one].astype(str), guide_col, c) >= 0.95]
        suffix = pd.Series(obs.index.astype(str)).str.rsplit("-", n=1).str[1]
        channel = suffix.to_numpy() if suffix.notna().all() and suffix.nunique() > 1 else None
        seen, channel_level, aliases = {}, [], {}
        for c in obs.columns:  # candidate units: categories that follow the barcode's channel suffix
            if c == guide_col or c in labels_from_guides or obs[c].dtype.kind in "fiu":
                continue
            v = obs[c].astype(str)
            sizes = v.value_counts()
            if not (2 <= len(sizes) <= 24 and sizes.min() >= 100):
                continue
            if channel is not None:
                t = pd.DataFrame({"ch": channel, "v": v.to_numpy()})
                if inv._derived(t, "ch", "v") < 0.95:
                    continue
            channel_level.append(c)
            split = tuple(pd.factorize(v)[0])
            if split in seen:  # the same split of cells under another name
                aliases.setdefault(seen[split], []).append(c)
                continue
            seen[split] = c
            candidates.append(c)
        for c in candidates:
            per = pd.crosstab(obs[c].astype(str)[one], g[one])
            name = f"'{c}'" + (f" (also {', '.join(repr(a) for a in aliases[c])})" if c in aliases else "")
            f, q = inv.unit_similarity(per, f"{name} values")
            facts += f
            qs += q
            ctrl = [x for x in per.columns if target_of.get(x) == "control"]
            f, q = inv.control_spread(per[ctrl], f"'{c}' values")
            facts += [x for x in f if x["key"] == "perturbation.control_bursts"]
            qs += [x for x in q if x["key"] == "perturbation.control_bursts"]
        allc = pd.crosstab(pd.Series("all", index=obs.index)[one], g[one])
        ctrl = [x for x in allc.columns if target_of.get(x) == "control"]
        f, q = inv.control_spread(allc[ctrl], "the object")
        facts += [x for x in f if x["key"] == "perturbation.control_spread"]
        qs += [x for x in q if x["key"] == "perturbation.library_representation"]
        has_guide = g.isin(set(guides)).to_numpy()
        best, best_rank = None, (0.0, 0.0)
        for c in obs.columns:  # the guide-UMI column: integer counts, positive in every cell with a guide, zero in
            if obs[c].dtype.kind not in "fiu":  # most cells without one, the largest such counts
                continue
            x = obs[c].to_numpy(float)
            if not has_guide.any() or np.nanmean(x[has_guide] > 0) < 0.99 or np.nanmax(np.abs(x - np.round(x))) > 1e-6:
                continue
            if (~has_guide).any() and np.nanmean(x[~has_guide] == 0) < 0.5:
                continue
            rank = (float(np.nanmedian(x[has_guide])), float(np.nanpercentile(x[has_guide], 99)))
            if rank > best_rank:  # UMI counts have the largest values and tail; counts of guides stay near 1
                best, best_rank = c, rank
        if best is not None:
            x = obs[best].to_numpy(float)
            pos = x[x > 0]
            facts.append(dt.fact("perturbation.call_umis", {"median": float(np.median(pos)),
                                                            "share_at_most_2": round(float(np.mean(pos <= 2)), 3),
                                                            "column": str(best), "cells_with_guide_umis": int(len(pos))},
                                 "observed", "the largest count column positive in every cell with a guide"))
            if np.median(pos) <= 2 or np.mean(pos <= 2) >= 0.5:
                qs.append(dt.question("perturbation.call_umis", f"Guide UMIs per cell are few (median "
                                      f"{np.median(pos):g}, {np.mean(pos <= 2):.0%} with 2 or fewer; column "
                                      f"'{best}'). Keep the deposit's calls, or require more UMIs (and lose cells)?",
                                      "calls on one UMI may not tell a guide from stray guide RNA"))
        no_guide = float((~has_guide).mean())
        if no_guide >= 0.5:
            facts.append(dt.fact("perturbation.cells_without_guide", round(no_guide, 3), "observed", "guide column",
                                 "cells without a guide are not a reference: they include perturbed cells whose guide "
                                 "was not captured"))
    f, q = dt.conditions(geo, None)
    facts += f
    qs += q
    f, q = inv.samples_inventory([os.path.basename(x) for x in all_files], geo, has_guide_data=bool(guide_col),
                                 one_object=True)
    facts += f
    qs += q
    annotation_cols = [c for c in obs.columns if obs[c].dtype.kind not in "fiu" and c != guide_col
                       and c not in channel_level and c not in labels_from_guides and 3 <= obs[c].nunique() <= 200]
    qs += inv.reading_questions(list(free_text), annotation_cols, False)
    qs.append(dt.question("perturbation.modality", "Confirm the modality: knockout, CRISPRi, CRISPRa, base editing, or mixed (guides of different modes in one library: then each guide's mode, in perturbation.guides).",
                          "the gate needs it confirmed"))
    qs.append(dt.question("units.independent_unit", "Which cell-metadata column holds the biological replicates "
                          "(mice, donors), if any?", "each result's n names the biological replicates (mice, donors) "
                          "apart from technical ones (lanes, wells)"))
    library = {"guides": [str(g) for g in guides], "cells_per_guide": {}, "target_of": {}, "targets_source": None,
               "reference_labels": dt.reference_labels(ref), "samples": [], "hashtags": [],
               "object_columns": [str(c) for c in obs.columns], "units": [], "genes": [str(g) for g in a.var_names]}
    return _result(dep, facts, qs, library)


def _result(dep, facts, qs, library=None, arrays=None):
    """{design, facts, questions, library, arrays}: library holds what the proposal checks need (design_audit.checks):
    the guide names, each guide's cells, the sample names, the hashtag-like features, an object's columns; arrays the
    per-cell calls and units and the per-guide expression summary (numpy arrays, for save_arrays)."""
    design = {"schema": SCHEMA, "dataset": {"data_location": dep["root"], "layout": dep["layout"]}}
    for f in facts:
        node = design
        parts = f["key"].split(".")
        for p in parts[:-1]:
            nxt = node.get(p)
            if not isinstance(nxt, dict) or "value" in nxt:
                nxt = {} if not isinstance(nxt, dict) else {"_": nxt}
                node[p] = nxt
            node = nxt
        node[parts[-1]] = {k: v for k, v in f.items() if k != "key"}
    design["unresolved"] = qs
    empty = {"guides": [], "cells_per_guide": {}, "target_of": {}, "targets_source": None,
             "samples": [s["name"] for s in dep["samples"]], "hashtags": [], "object_columns": [], "units": [],
             "genes": []}
    return {"design": design, "facts": facts, "questions": qs, "library": library or empty, "arrays": arrays or {}}


def save_arrays(arrays, path):
    """The arrays as one compressed npz file (written whole, then moved into place)."""
    with open(path + ".tmp", "wb") as fh:
        np.savez_compressed(fh, **arrays)
    os.replace(path + ".tmp", path)


def load_arrays(path):
    if not os.path.exists(path):
        return None
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_plain(v) for v in x]
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating):
        return float(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.str_):
        return str(x)
    if isinstance(x, np.ndarray):
        return [_plain(v) for v in x.tolist()]
    return x


def write_design(result, path):
    import yaml
    with open(path, "w") as fh:
        yaml.safe_dump(_plain(result["design"]), fh, sort_keys=False, allow_unicode=True, width=120)
