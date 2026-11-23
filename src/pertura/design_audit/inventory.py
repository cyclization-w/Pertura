"""Inventory detectors: deposit problems that mislead an analysis, measured from the files without reading meaning
from names. Each returns (facts, questions), as the detectors in detectors.py do.

- samples_inventory: per GEO sample, the kinds of files the deposit holds for it; samples with no matrix; a deposit
  with no guide data at all.
- raw_droplets: matrices of every droplet rather than of called cells.
- matrix_content: whether a processed matrix holds counts, log counts or normalised values.
- type_conflicts: guide features a matrix declares as gene expression.
- unannotated_features: expression features outside the annotation's ID format, and whether they behave like guides.
- guide_sources: guide-only matrices against the expression matrices, by barcode overlap.
- guide_reliability: per unit, cells with a readable guide, cells with a call, ambiguous calls, cells with several
  targets, and the calls' UMI support.
- cooccurrence: guides that travel together (two guides of one target in one vector; targets delivered as sets),
  cells carrying guides of two sets, and guide names one edit apart that always co-occur.
- unit_similarity: units whose guide make-up is nearly identical (one animal split in two).
- hashtags_within_units: units holding several hashtag groups.
- unit_specific_guides: guides present in only some units.
- control_spread: control cells per unit, bursts of one control guide, and the spread of cells per control guide.
- testable_pairs: in a combinatorial library, the pairs whose two singles share a unit with them.
- label_disagreements: a guide's label in the feature reference against the label cell metadata give its cells.
- cell_metadata: tables keyed by the matrix's barcodes, their guide columns, and labels derived from those.
- aggregation_order: barcode suffixes of a combined matrix against its aggregation file and per-channel calls.
- factor_confounding: unit-level factors where a level of one occurs under a single level of another.
- reading_questions: questions only reading can settle (pooled-in populations, outside dependencies, free-text
  files, cell annotations, hashtags the metadata do not list).
"""
import os
import re

import numpy as np
import pandas as pd
import scipy.sparse as sp

from ..most_common import most_common
from .detectors import fact, question
from .readers import GUIDE

GSM = re.compile(r"GSM\d+")  # GEO's sample accession format
RAW_MIN_BARCODES = 50_000    # a filtered 10x matrix rarely holds this many cells per sample
RAW_LOW_UMIS = 100           # ... and a raw one is mostly droplets with fewer UMIs than this
RAW_LOW_SHARE = 0.8
INTEGER_TOL = 1e-3
MAX_COOCCURRENCE_GUIDES = 5000  # guides x guides co-occurrence is held dense
GUIDE_NUMBER = re.compile(r"[-_.]?\d+$")  # a guide's number at the end of its name
DOUBLET_UMIS = 1.3              # cells of two sets hold at least this many times one set's cells' UMIs to be doublets
EXCLUSIVE_JACCARD = 0.05        # features sharing fewer cells than this (Jaccard) count as mutually exclusive


# 1. what the deposit holds ------------------------------------------------------------------------------------------
def _kind(path):
    b = os.path.basename(path)
    if b.endswith((".h5",)):
        return "h5 matrix"
    if b.endswith(("matrix.mtx.gz", "matrix.mtx")):
        return "matrix"
    if b.endswith(("barcodes.tsv.gz", "barcodes.tsv")):
        return "barcodes"
    if b.endswith(("features.tsv.gz", "features.tsv", "genes.tsv.gz", "genes.tsv")):
        return "features"
    if "protospacer" in b:
        return "guide calls"
    return "other"


def samples_inventory(files, geo, has_guide_data, one_object=False):
    """files: every file of the deposit (extracted archives included), as paths below the deposit; geo:
    read_geo_soft's result or None; has_guide_data: whether any guide feature, call file or guide column was found;
    one_object: the deposit is one processed object standing for every sample."""
    facts, qs = [], []
    by = {}
    for f in files:
        for g in set(GSM.findall(f)):
            by.setdefault(g, set()).add(_kind(f))
    listed = list(geo["samples"]) if geo else sorted(by)
    matrices_named = any(_kind(f) in ("matrix", "h5 matrix") and GSM.search(f) for f in files)
    if listed and not one_object and not matrices_named and any(_kind(f) in ("matrix", "h5 matrix") for f in files):
        facts.append(fact("data.files_not_linked_to_samples", {"geo_samples": len(listed)}, "observed",
                          "file names", "no matrix is named by a GEO sample: which sample each file holds must be read"))
        qs.append(question("data.files_not_linked_to_samples", f"No matrix file is named by one of the {len(listed)} "
                           "GEO samples. Which sample (animal, condition) does each file hold?",
                           "files nobody can link to a sample are units nobody can name"))
        listed = []
    if listed and not one_object:
        table = {g: sorted(by.get(g, set())) for g in listed}
        no_matrix = [g for g, k in table.items() if not ({"matrix", "h5 matrix", "guide calls"} & set(k))]
        facts.append(fact("data.samples_files", {"samples": len(table), "with_a_matrix": len(table) - len(no_matrix),
                                                 "files": table}, "observed", "file names (GEO sample IDs)"))
        if no_matrix and len(no_matrix) < len(table) + 1:
            what = {g: table[g] or ["nothing"] for g in no_matrix[:8]}
            titles = {g: geo["samples"][g]["title"] for g in no_matrix[:8]} if geo else {}
            facts.append(fact("data.samples_without_matrix", {"n": len(no_matrix), "holds": what, "titles": titles},
                              "observed", "file names (GEO sample IDs)",
                              "these samples cannot be analysed from this deposit"))
            qs.append(question("data.samples_without_matrix",
                               f"{len(no_matrix)} of {len(table)} samples have no expression matrix in the deposit "
                               f"(e.g. {', '.join(no_matrix[:3])}). Are they needed, and do their data exist "
                               "elsewhere?", "say early what cannot be analysed"))
    if not has_guide_data:
        facts.append(fact("perturbation.no_guide_data", True, "observed", "matrices, call files and cell metadata",
                          "no guide features, guide calls or guide columns: no cell can be assigned a perturbation"))
        qs.append(question("perturbation.guide_data", "The deposit holds no guide data (no guide features, calls or "
                           "guide columns). Where are the perturbation labels (another deposit, a supplement, guide "
                           "probes in a fixed-RNA kit)?", "without them nothing about perturbations can be answered"))
    return facts, qs


def raw_droplets(per_sample_umis):
    """per_sample_umis: sample -> gene-expression UMIs of every barcode in its matrix."""
    raw = {}
    for s, u in per_sample_umis.items():
        u = np.asarray(u)
        if len(u) >= RAW_MIN_BARCODES and float((u < RAW_LOW_UMIS).mean()) >= RAW_LOW_SHARE:
            raw[s] = {"barcodes": int(len(u)), "with_100_umis_or_more": int((u >= RAW_LOW_UMIS).sum())}
    if not raw:
        return [], []
    return [fact("data.raw_droplets", raw, "observed", "UMIs per barcode",
                 "most barcodes are empty droplets: cells must be called before any analysis")], [
        question("data.raw_droplets", f"{len(raw)} matrices hold every droplet, not called cells "
                 f"({', '.join(list(raw)[:3])}). Is there a filtered matrix, or should cells be called (Cell Ranger's "
                 "or an empty-droplet test; or data.cell_calling: {min_umis, min_genes})?",
                 "empty droplets would dilute every group and its guide calls")]


# two samples hold one capture's cells when they share this share of the smaller one's barcodes; different captures of
# one deposit share about 1% (GSE250558: 138 of 21,695), its two libraries of one capture 99.9% (21,676)
SAME_CELLS_SHARE = 0.9


def same_cells(barcodes_by_sample):
    """barcodes_by_sample: sample -> its cells' barcodes. Pairs of samples holding one capture's cells, as two
    libraries of each capture deposited apart (GSE250558): a fact, and the question of which one is read."""
    names, pairs = sorted(barcodes_by_sample), []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            A, B = barcodes_by_sample[a], barcodes_by_sample[b]
            shared = len(A & B)
            if A and B and shared >= SAME_CELLS_SHARE * min(len(A), len(B)):
                pairs.append({"samples": [a, b], "shared_barcodes": shared, "cells": [len(A), len(B)]})
    if not pairs:
        return [], []
    eg = pairs[0]
    return [fact("data.same_cells_in_several_samples", pairs, "observed", "barcodes",
                 "each pair holds one capture's cells")], [
        question("data.same_cells_in_several_samples", f"{len(pairs)} pairs of samples hold the same cells "
                 f"({eg['samples'][0]} and {eg['samples'][1]}: {eg['shared_barcodes']} barcodes in common). Which one "
                 "is each capture's cells read from? Take the other out of the reading (cell_table.remove, with why).",
                 "a capture read twice counts its cells and its unit twice")]


def matrix_content(values, row_sums_stored=None, marked_log=False):
    """values: a dense block of a matrix's rows (cells x features). row_sums_stored: {column: per-row totals} that
    cell metadata store for the same rows. marked_log: the object's metadata say it is log-transformed."""
    v = np.asarray(values, dtype=np.float64)
    nz = v[v != 0]
    if not len(nz):
        return [], []
    integer = float(np.mean(np.abs(nz - np.round(nz)) < INTEGER_TOL)) > 0.99
    e = np.expm1(v)
    enz = e[v != 0]
    expm1_integer = float(np.mean(np.abs(enz - np.round(enz)) < 1e-2)) > 0.99
    sums, esums = v.sum(1), e.sum(1)
    note, what = None, "unknown"
    if integer:
        what = "counts"
    elif expm1_integer:
        what = "log(1 + counts)"
        for col, tot in (row_sums_stored or {}).items():
            t = np.asarray(tot, dtype=np.float64)
            if len(t) == len(esums) and np.allclose(esums, t, rtol=0.01):
                note = f"exp(X) - 1 sums to the stored '{col}' in every cell: raw counts, log-transformed"
                break
    elif np.std(esums) < 0.01 * max(np.mean(esums), 1e-9):
        what = f"log(1 + normalised to {np.mean(esums):.0f} per cell)"
    elif np.std(sums) < 0.01 * max(np.mean(sums), 1e-9):
        what = f"normalised to {np.mean(sums):.0f} per cell"
    facts = [fact("data.matrix_content", what, "observed", "matrix values", note)]
    qs = []
    if what.startswith("log(1 + counts)") and marked_log:
        facts.append(fact("data.matrix_content_vs_metadata", "log of raw counts, not of normalised counts",
                          "observed", "matrix values and the object's metadata",
                          "the object's metadata mark it log-normalised, but its values are log raw counts"))
    if what != "counts":
        qs.append(question("data.matrix_content", f"The matrix holds {what}, not counts. Use exp(X) - 1 as counts "
                           "where they are recoverable, and normalise once?",
                           "normalising twice, or testing logs as counts, distorts every comparison"))
    return facts, qs


def type_conflicts(conflicts):
    """conflicts: per matrix file, guide features its type column declares as gene expression (readers)."""
    if not conflicts:
        return [], []
    n = sum(c["n"] for c in conflicts)
    return [fact("data.guides_declared_as_genes", conflicts, "observed", "matrix features against the feature "
                 "reference", f"{n} features the reference declares as guides are typed 'Gene Expression' in the "
                 "matrix; they are read as guides")], [
        question("data.guides_declared_as_genes", f"{n} guide features are typed as genes in the matrix. Keep them "
                 "out of the gene expression before normalising?", "counted as genes they enter every DE test")]


def _id_shape(s):
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", str(s)))


def guide_key(name):
    """A guide's name without case, punctuation or a last word that is not a number (gRNA_IL-17A_1_gene and
    gRNA_Il17a_1_capture give one key: a library read once as gene expression and once by guide capture)."""
    parts = [p for p in re.split(r"[^0-9a-z]+", str(name).lower()) if p]
    if len(parts) > 1 and not parts[-1].isdigit():
        parts = parts[:-1]
    return "".join(parts)


def library_matches(names, library):
    """{name: library guide} for each name whose key (guide_key) is one library guide's alone."""
    keys = {}
    for g in library:
        keys.setdefault(guide_key(g), []).append(str(g))
    return {str(n): keys[guide_key(n)][0] for n in names if len(keys.get(guide_key(n), [])) == 1}


def unannotated_features(ids, names, X, known_guides=()):
    """ids, names: the gene-expression features (columns of X, a cells x features subset). Features whose ID shape
    differs from the shape most IDs share (an annotation's 'ENSMUSG' + digits) were added to the reference; when a
    few of them dominate the cells that carry any (as guides do), they look like guides counted as genes."""
    ids, names = np.asarray(ids, dtype=str), np.asarray(names, dtype=str)
    if not len(ids):
        return [], []
    shapes = pd.Series([_id_shape(i) for i in ids])
    top = shapes.value_counts()
    if top.iloc[0] < 0.9 * len(ids):
        return [], []
    odd = np.flatnonzero(shapes.to_numpy() != top.index[0])
    named_guides = np.flatnonzero(np.isin(names, list(known_guides))) if len(known_guides) else np.array([], int)
    odd = np.union1d(odd, named_guides)
    if not len(odd) or len(odd) > 5000:
        return [], []
    Xo = sp.csr_matrix(X)[:, odd]
    # features detected in the same cells (one transcript's pieces, a reporter) form groups; guides are detected
    # alone, one per cell
    B = (Xo > 0).astype(np.float32)
    n = np.asarray(B.sum(0)).ravel()
    C = (B.T @ B).toarray()
    J = C / np.maximum(n[:, None] + n[None, :] - C, 1)
    np.fill_diagonal(J, 0)
    from scipy.sparse.csgraph import connected_components
    k, lab = connected_components(sp.csr_matrix(J >= 0.3), directed=False)
    sizes = np.bincount(lab, minlength=k)
    grouped = np.flatnonzero(sizes[lab] > 1)
    # the mutually exclusive set, built greedily from the most detected features: a feature joins when it shares
    # almost no cell with any member (guides do not share cells; a reporter's pieces, or a reporter and the guides
    # on its vector, do)
    alone = []
    for j in np.argsort(-n):
        if n[j] >= 5 and all(J[j, m] < EXCLUSIVE_JACCARD for m in alone):
            alone.append(int(j))
    alone = np.array(sorted(alone), dtype=int)
    if len(alone) >= 3:  # judge the exclusive set, without the features that share cells
        odd_all, odd = odd, odd[alone]
        Xo = Xo[:, alone]
    else:
        odd_all = odd
    tot = np.asarray(Xo.sum(1)).ravel()
    carry = tot >= 2
    dom = None
    if carry.sum() >= 30 and len(odd) >= 3:
        dom = float(np.mean(np.asarray(Xo[carry].max(1).todense()).ravel() / tot[carry] >= 0.7))
    value = {"n": int(len(odd_all)), "detected_alone": int(len(odd)), "examples": names[odd][:8].tolist(),
             "co_expressed_groups": [names[odd_all[lab == c]][:6].tolist() for c in np.unique(lab[grouped])][:4]
             if len(grouped) else [], "cells_carrying": int(carry.sum()),
             "share_of_cells_carrying": round(float(carry.mean()), 4), "all_names": names[odd_all].tolist()[:500],
             "one_dominates_share": None if dom is None else round(dom, 3),
             "named_like_guides": int(len(named_guides))}
    detected = np.asarray((Xo > 0).mean(0)).ravel()
    sparse_set = len(odd) >= 5 and detected.max() < 0.05 and dom is not None and dom >= 0.5  # guides of 1-2 UMIs
    guide_like = len(named_guides) > 0 or (dom is not None and dom >= 0.6) or sparse_set
    matches = library_matches(names[odd_all], known_guides) if guide_like else {}
    value.update({"guide_like": bool(guide_like), "library_matches": matches})
    facts = [fact("data.unannotated_features", value, "observed", "feature IDs and their counts",
                  "added to the gene annotation; one of them dominates each cell that carries any, as guides do"
                  if guide_like else "added to the gene annotation (reporters, transgenes or guides)")]
    qs = []
    if guide_like and carry.mean() < 0.2:
        facts.append(fact("perturbation.readable_fraction", round(float(carry.mean()), 4), "observed",
                          "the guide-like features' counts", "few cells carry a readable guide"))
        qs.append(question("perturbation.readable_fraction", f"Only {carry.mean():.1%} of cells carry one of the "
                           "guide-like features. Is there a guide-enrichment library elsewhere, or is the screen this "
                           "small?", "the assignable cells set the screen's real size"))
    if guide_like:
        same = (f" {len(matches)} of them match the library's guides by name (such as "
                f"{next(iter(matches))} = {matches[next(iter(matches))]})." if matches else "")
        qs.append(question("data.guides_among_genes", f"{len(odd)} expression features look like guides "
                           f"({', '.join(names[odd][:4])}).{same} Are they the guide readout? Then read them as "
                           "guides in the samples they belong to (cell_table.retype, with the library's names), so "
                           "they leave the genes and give the calls.", "guides counted as genes inflate DE and hide "
                           "the screen's best guide source"))
    return facts, qs


# 2. guides ----------------------------------------------------------------------------------------------------------
def guide_sources(sources, expression_cells):
    """sources: name -> {"barcodes": set, "assigned": number of those cells with a dominant guide or None};
    expression_cells: sample -> set of barcodes of an expression matrix. Every guide source is compared with every
    expression matrix by the share of the expression cells it covers."""
    rows = {}
    for name, s in sources.items():
        cover = {e: round(len(s["barcodes"] & bcs) / max(len(bcs), 1), 3) for e, bcs in expression_cells.items()}
        best = max(cover, key=cover.get) if cover else None
        rows[name] = {"barcodes": len(s["barcodes"]), "best_match": best,
                      "covers_share_of_its_cells": cover.get(best, 0.0) if best else 0.0,
                      "cells_assigned": s.get("assigned")}
    if not rows:
        return [], []
    facts = [fact("perturbation.guide_sources", rows, "observed", "barcode overlap with the expression matrices")]
    qs = []
    poor = {n: r for n, r in rows.items() if r["covers_share_of_its_cells"] < 0.5}
    if poor:
        facts.append(fact("perturbation.guide_sources_poor", sorted(poor), "observed", "barcode overlap",
                          "these guide sources cover under half of any expression matrix's cells"))
        qs.append(question("perturbation.guide_sources", f"{len(poor)} guide sources share few barcodes with the "
                           f"expression matrices ({', '.join(sorted(poor)[:3])}). Use the source that covers the "
                           "most cells (guide features inside the expression files, when they exist)?",
                           "the wrong guide source loses most of the screen"))
    return facts, qs


def _call_stats(calls, target_of):
    """Per cell with a call: its number of targets; every call's UMIs."""
    n_t, umis = {}, []
    for bc, v in calls.items():
        n_t[bc] = len({target_of.get(g, g) for g, _ in v})
        umis += [u for _, u in v]
    return n_t, np.asarray(umis)


def guide_reliability(cells, unit, guide_umis_per_cell, calls, target_of, combinatorial_share=None):
    """cells, unit: every cell (likely cells only, when a matrix holds every droplet) and its candidate unit;
    guide_umis_per_cell: total guide UMIs per cell (or None without guide features); calls: barcode -> [(guide,
    umis)], the deposit's calls or Cell Ranger's method's (the build's default)."""
    n_t, umis = _call_stats(calls, target_of)
    df = pd.DataFrame({"unit": np.asarray(unit, dtype=str)}, index=np.asarray(cells, dtype=str))
    df["called"] = df.index.isin(list(calls))
    df["several"] = df.index.map(lambda b: n_t.get(b, 0) > 1)
    if guide_umis_per_cell is not None:
        df["readable"] = np.asarray(guide_umis_per_cell) > 0
    g = df.groupby("unit")
    called = g["called"].sum()
    per = pd.DataFrame({"cells": g.size(), "with_a_call": (called / g.size()).round(3),
                        "several_targets": (g["several"].sum() / called.clip(lower=1)).round(3)})
    if "readable" in df:
        per["with_guide_umis"] = g["readable"].mean().round(3)
    facts = [fact("perturbation.guides_by_unit", {str(u): {k: (None if pd.isna(x) else float(x)) for k, x in r.items()}
                                                  for u, r in per.iterrows()}, "observed", "guide calls per unit",
                  None if target_of else "no target map yet: each guide stands for a target")]
    qs = []
    share_called = float(df["called"].mean())
    readable = float(df["readable"].mean()) if "readable" in df else share_called
    if readable < 0.2:
        facts.append(fact("perturbation.readable_fraction", round(readable, 3), "observed", "guide UMIs per cell",
                          "few cells carry a readable guide: most of the data cannot be assigned"))
        qs.append(question("perturbation.readable_fraction", f"Only {readable:.1%} of cells carry a guide. Is there a "
                           "guide-enrichment library elsewhere, or is the screen this small?",
                           "the assignable cells set the screen's real size"))
    if 1 - share_called >= 0.5:
        facts.append(fact("perturbation.cells_without_guide", round(1 - share_called, 3), "observed", "guide calls",
                          "cells without a guide are not a reference: they include perturbed cells whose guide was "
                          "not captured"))
    big = per[per["cells"] >= 100]
    if combinatorial_share is not None:  # units where most cells carry several targets by design are compared apart
        big = big[big["several_targets"] < combinatorial_share]
    if len(big) > 1:
        lo, hi = float(big["several_targets"].min()), float(big["several_targets"].max())
        if hi - lo >= 0.1 and hi >= 1.3 * max(lo, 1e-9):
            facts.append(fact("perturbation.ambient_guides_differ", {"lowest": lo, "highest": hi,
                                                                    "by_unit": big["several_targets"].to_dict()},
                              "observed", "guide calls per unit",
                              "units differ in cells called with several targets: ambient guide RNA differs"))
            qs.append(question("perturbation.call_rule", "Cells called with several targets range from "
                               f"{lo:.0%} to {hi:.0%} across units. Which calling method should assign the guides "
                               "(the deposit's calls, Cell Ranger's own method, pertpy's mixture model, or crispat's "
                               "methods in your own code)? Compare what each keeps per unit before choosing.",
                               "a calling method that ambient guide RNA sways can dilute some samples' groups more "
                               "than others"))
    if len(umis):
        med, low = float(np.median(umis)), float(np.mean(umis <= 2))
        facts.append(fact("perturbation.call_umis", {"median": med, "share_at_most_2": round(low, 3)}, "observed",
                          "guide calls"))
        if med <= 2:
            qs.append(question("perturbation.call_umis", f"Guide calls rest on a median of {med:g} UMIs ({low:.0%} on 2 "
                               "or fewer). Keep the deposit's calls, or require more UMIs (and lose cells)?",
                               "calls on one UMI may not tell a guide from stray guide RNA"))
    return facts, qs


def _edit1(a, b):
    """True when a and b differ by one substitution, insertion, deletion or swap of two neighbouring letters, and
    not only in their digits (guide numbers: 'rxraa-2' and 'rxraa-3' are two guides of one target)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if re.sub(r"\d", "", a) == re.sub(r"\d", "", b):
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def cooccurrence(A, guides, target_of, total_umis=None, gene_names=None):
    """A: cells x guides (1 where a guide is called); total_umis: gene-expression UMIs per cell (for doublets);
    gene_names: the matrix's genes, against which a target name that is no gene, one edit from one that is and
    called with it, is a typo (two paralogs, both genes, are not)."""
    A = sp.csr_matrix(A).astype(np.float32)
    guides = [str(g) for g in guides]
    if A.shape[1] < 2 or A.nnz == 0:
        return [], []
    if A.shape[1] > MAX_COOCCURRENCE_GUIDES:
        return [fact("perturbation.cooccurrence", "not measured", "observed", "guide calls",
                     f"more than {MAX_COOCCURRENCE_GUIDES} guides")], []
    n = np.asarray(A.sum(0)).ravel()
    C = (A.T @ A).toarray()
    J = C / np.maximum(n[:, None] + n[None, :] - C, 1)
    np.fill_diagonal(J, 0)
    facts, qs = [], []
    # one vector, two guides of one target
    # without a target map, a guide's name less its trailing number ('tfec-2' -> 'tfec') stands for its target
    tgt = [str(target_of.get(g, g)) if target_of else GUIDE_NUMBER.sub("", g) or g for g in guides]
    by_t = {}
    for j, t in enumerate(tgt):
        by_t.setdefault(t, []).append(j)
    multi = {t: js for t, js in by_t.items() if len(js) >= 2 and t != "control"}
    if target_of and multi:
        paired = [t for t, js in multi.items() if J[np.ix_(js, js)].max() >= 0.5]
        if len(paired) >= 0.5 * len(multi):
            facts.append(fact("perturbation.vector_structure", {"targets_with_guides_together": len(paired),
                                                               "of": len(multi)}, "observed", "guide co-occurrence",
                              "guides of one target are called together: each vector carries two of them; count "
                              "targets, not guides, per cell"))
    # fixed pairs without a map: among cells with exactly two guides, most guides have one partner, and the
    # partner's partner is the guide (two guides on one vector)
    two = np.flatnonzero(A.getnnz(axis=1) == 2)
    if len(two) >= 200 and not (target_of and multi):
        P = A[two]
        C2 = (P.T @ P).toarray()
        np.fill_diagonal(C2, 0)
        n2 = C2.sum(1)
        top = C2.argmax(1)
        share = np.where(n2 > 0, C2[np.arange(len(guides)), top] / np.maximum(n2, 1), 0)
        seen2 = n2 >= 10
        fixed = [j for j in np.flatnonzero(seen2) if share[j] >= 0.5 and top[top[j]] == j]
        if seen2.sum() >= 10 and len(fixed) >= 0.5 * seen2.sum():
            pairs = sorted({tuple(sorted((guides[j], guides[top[j]]))) for j in fixed})
            facts.append(fact("perturbation.guide_pairs", {"guides_in_fixed_pairs": len(fixed), "of": int(seen2.sum()),
                                                          "examples": [list(p) for p in pairs[:5]]}, "observed",
                              "partners among cells with exactly two guides",
                              "guides come in fixed pairs: each vector carries two guides, so a cell with both is one "
                              "perturbation, not two"))
            qs.append(question("perturbation.vector_pairs", f"{len(fixed)} of {int(seen2.sum())} guides come in fixed "
                               f"pairs (e.g. {pairs[0][0]} with {pairs[0][1]}). Is each pair one vector (two guides of "
                               "one target)? Then count targets, not guides, and keep the cells with both.",
                               "removing two-guide cells would remove most of the screen"))
    # names one edit apart that always co-occur
    typos = [(guides[i], guides[j], int(C[i, j])) for i in range(len(guides)) for j in range(i + 1, len(guides))
             if J[i, j] >= 0.9 and C[i, j] >= 20 and _edit1(guides[i], guides[j])]
    if typos:
        facts.append(fact("perturbation.guide_name_typos", [list(t) for t in typos[:10]], "observed",
                          "guide names and co-occurrence", "one edit apart and almost always called together: "
                          "one guide under two names?"))
        qs.append(question("perturbation.guide_name_typos", f"{len(typos)} pairs of guide names differ by one letter "
                           f"and are always called together (e.g. {typos[0][0]} and {typos[0][1]}). Are they one "
                           "guide?", "a name typo splits one group in two"))
    # targets delivered together (sets), from the target-level co-occurrence
    T_names = sorted(by_t)
    tindex = {t: i for i, t in enumerate(T_names)}
    if len(T_names) >= 2:
        M = sp.csr_matrix((np.ones(A.nnz, np.float32), (np.repeat(np.arange(A.shape[0]), np.diff(A.indptr)),
                                                        np.array([tindex[tgt[j]] for j in A.indices]))),
                          shape=(A.shape[0], len(T_names)))
        M.data[:] = 1
        M.sum_duplicates()
        M.data[:] = 1
        nt = np.asarray(M.sum(0)).ravel()
        Ct = (M.T @ M).toarray()
        Jt = Ct / np.maximum(nt[:, None] + nt[None, :] - Ct, 1)
        np.fill_diagonal(Jt, 0)
        edges = (Jt >= 0.5) & (Ct >= 20)
        from scipy.sparse.csgraph import connected_components
        k, lab = connected_components(sp.csr_matrix(edges), directed=False)
        sets = [[T_names[i] for i in np.flatnonzero(lab == c)] for c in range(k) if (lab == c).sum() >= 2]
        sets = [s for s in sets if "control" not in s]
        genes = set(gene_names or ())
        within = Ct / np.maximum(np.minimum(nt[:, None], nt[None, :]), 1)  # the smaller name's cells carrying both
        stem_typos = [(T_names[i], T_names[j], int(Ct[i, j])) for i in range(len(T_names))
                      for j in range(i + 1, len(T_names))
                      if Ct[i, j] >= 20 and _edit1(T_names[i], T_names[j])
                      and (Jt[i, j] >= 0.9 or (genes and within[i, j] >= 0.9
                                                and (T_names[i] in genes) != (T_names[j] in genes)))]
        if stem_typos and not typos:
            facts.append(fact("perturbation.guide_name_typos", [list(t) for t in stem_typos[:10]], "observed",
                              "target names and co-occurrence", "one edit apart and almost always called "
                              "together: one target under two names?"))
            qs.append(question("perturbation.guide_name_typos", f"{len(stem_typos)} pairs of target names differ by "
                               f"one letter and are always called together (e.g. {stem_typos[0][0]} and "
                               f"{stem_typos[0][1]}). Are they one target?", "a name typo splits one group in two"))
        if sets:
            facts.append(fact("perturbation.delivered_sets", sets, "observed", "target co-occurrence",
                              "targets almost always called together: delivered as sets (co-injected guides, or "
                              "one vector), so the perturbation unit is the set"))
            qs.append(question("perturbation.sets", f"{len(sets)} groups of targets are called together (e.g. "
                               f"{' + '.join(sets[0])}). Were they delivered as sets? Then each set is one "
                               "perturbation, and cells showing one member are incomplete calls.",
                               "naming cells after one member mislabels set knockouts as single ones"))
            member = {t: i for i, s in enumerate(sets) for t in s}
            in_set = np.zeros((A.shape[0], len(sets)), bool)
            for ti, t in enumerate(T_names):
                if t in member:
                    in_set[np.asarray(M[:, ti].todense()).ravel() > 0, member[t]] = True
            two = in_set.sum(1) >= 2
            if two.sum() >= 10:
                v = {"cells": int(two.sum()), "share_of_set_cells": round(float(two.sum() / max(in_set.any(1).sum(), 1)), 3)}
                ratio = None
                if total_umis is not None:
                    tu = np.asarray(total_umis, float)
                    one = in_set.sum(1) == 1
                    ratio = float(np.median(tu[two]) / max(np.median(tu[one]), 1))
                    v["umis_vs_one_set"] = round(ratio, 2)
                if ratio is None or ratio >= DOUBLET_UMIS:  # doublets hold the RNA of two cells
                    facts.append(fact("perturbation.cross_set_doublets", v, "observed", "set co-occurrence and UMIs",
                                      "cells carrying guides of two sets, with more RNA than one cell: doublets"))
    return facts, qs


# 3. units -----------------------------------------------------------------------------------------------------------
def unit_similarity(counts, label):
    """counts: units x guides (cells with one called guide). Two units whose guide make-up is far more alike than
    the other pairs (the same clonal expansions) are likely one animal or culture split in two."""
    c = counts[counts.sum(1) >= 200]
    if len(c) < 3:
        return [], []
    P = c.to_numpy(float)
    P = P / np.linalg.norm(P, axis=1, keepdims=True)
    S = P @ P.T
    iu = np.triu_indices(len(c), 1)
    med = float(np.median(S[iu]))
    pairs = [(str(c.index[i]), str(c.index[j]), round(float(S[i, j]), 3)) for i, j in zip(*iu)
             if S[i, j] >= 0.9 and S[i, j] - med >= 0.25]
    if not pairs:
        return [], []
    return [fact("units.near_identical", {"by": label, "pairs": [list(p) for p in pairs], "median_pair": round(med, 3)},
                 "observed", "cells per guide in each unit",
                 "far more alike than other pairs: one animal or culture counted twice?")], [
        question("units.near_identical", f"{label} {pairs[0][0]} and {pairs[0][1]} have nearly the same guide make-up "
                 f"(similarity {pairs[0][2]} against a median of {med:.2f}). Are they one animal (two channels)?",
                 "counting one animal twice doubles its weight in every by-unit test")]


def hashtags_within_units(unit, tag, label):
    """unit, tag: per cell (tag '' for cells no hashtag dominates). Units holding several hashtag groups."""
    df = pd.DataFrame({"u": np.asarray(unit, dtype=str), "t": np.asarray(tag, dtype=str)})
    df = df[df["t"] != ""]
    if df.empty:
        return [], []
    share = df.groupby("u")["t"].value_counts(normalize=True)
    groups = {u: sorted(share[u][share[u] >= 0.05].index.tolist()) for u in df["u"].unique()}
    several = {u: g for u, g in groups.items() if len(g) >= 2}
    if not several:
        return [], []
    return [fact("units.hashtags_within", {"by": label, "groups": several}, "observed", "hashtag counts per unit",
                 "each of these units holds several hashtag groups: if hashtags mark animals or conditions, the "
                 "hashtag group, not the unit, is the unit of analysis")], []


def unit_specific_guides(counts, label, min_cells=10):
    """counts: units x guides. Guides present (min_cells) in some units but absent (no cell) from most others."""
    if len(counts) < 3:
        return [], []
    present = counts >= min_cells
    absent = counts == 0
    k = present.sum(0)
    few = [g for g in counts.columns if 0 < k[g] <= len(counts) / 2 and absent[g].sum() >= len(counts) / 2]
    if not few or len(few) == len(counts.columns):
        return [], []
    few = [str(g) for g in few]
    where = {g: [str(u) for u in counts.index[present[g]]] for g in few[:12]}
    return [fact("perturbation.unit_specific_guides", {"by": label, "n": len(few), "present_in": where},
                 "observed", "cells per guide in each unit",
                 "guides present in only some units: units without a group cannot compare with it")], [
        question("perturbation.unit_specific_guides", f"{len(few)} guides appear in only some {label} (e.g. "
                 f"{few[0]} in {len(where[str(few[0])])} of {len(counts)}). If any are controls, which units lack a "
                 "matched reference, and do the units differ otherwise (stage, condition)?",
                 "a sample without control cells is compared with all other perturbed cells, and the report says "
                 "so")]


def guides_by_sample(counts, min_cells=3, pairs_listed=60):
    """counts: samples x guides (the cells each guide is called in, per sample). The guides each sample holds (called
    in at least min_cells of its cells), the groups of samples that hold the same set, and the guides each pair of
    samples shares, the least shared first. Numbers only, and no question: whether the samples share one library is
    the design auditor's to read, with the GEO text and the sample table."""
    if len(counts) < 2 or counts.shape[1] == 0:
        return [], []
    held = {str(s): frozenset(str(g) for g in counts.columns[(counts.loc[s] >= min_cells).to_numpy()])
            for s in counts.index}
    same = {}
    for s, g in held.items():
        same.setdefault(g, []).append(s)
    names = list(held)
    pairs = [{"samples": [a, b], "shared": len(held[a] & held[b]), "only_first": len(held[a] - held[b]),
              "only_second": len(held[b] - held[a])} for i, a in enumerate(names) for b in names[i + 1:]]
    pairs.sort(key=lambda p: p["shared"] / max(1, min(len(held[p["samples"][0]]), len(held[p["samples"][1]]))))
    value = {"min_cells": min_cells, "guides_held": {s: len(g) for s, g in held.items()},
             "groups_with_the_same_set": [sorted(v) for v in same.values()], "pairs": pairs[:pairs_listed]}
    if len(pairs) > pairs_listed:
        value["pairs_more"] = len(pairs) - pairs_listed
    return [fact("data.guides_by_sample", value, "observed", "guide calls per sample",
                 f"a guide counts in a sample when at least {min_cells} of its cells carry it")], []


def control_spread(control_counts, label):
    """control_counts: units x control guides (cells with one called guide). Bursts: one control guide holding most
    of a unit's control cells (a clone). Spread: cells per control guide over the whole screen."""
    facts, qs = [], []
    if control_counts.shape[1] == 0:
        return facts, qs
    per_unit = control_counts.sum(1)
    facts.append(fact("perturbation.control_cells_by_unit", {str(u): int(v) for u, v in per_unit.items()},
                      "observed", f"control cells per {label}"))
    ok = control_counts[per_unit >= 20]
    if len(ok):
        top = (ok.max(1) / ok.sum(1)).round(3)
        burst = top[top >= 0.5]
        if len(burst):
            facts.append(fact("perturbation.control_bursts", {str(u): {"top_guide": str(ok.loc[u].idxmax()),
                                                                      "share": float(top[u])} for u in burst.index},
                              "observed", f"control cells per guide and {label}",
                              "one control guide holds most of the unit's control cells: the reference is one clone"))
            qs.append(question("perturbation.control_bursts", f"In {len(burst)} {label}, one control guide holds half "
                               "or more of the control cells. Use control guides (not cells) as the replicates, or "
                               "all other perturbed cells as the reference?",
                               "a clonal control moves every target together"))
    tot = control_counts.sum(0)
    tot = tot[tot > 0]
    if len(tot) >= 3 and tot.max() >= 3 * tot.min():
        facts.append(fact("perturbation.control_spread", {"min": int(tot.min()), "max": int(tot.max()),
                                                          "guides": len(tot)}, "observed", "cells per control guide",
                          "control guides differ several-fold in cells: abundance is not fitness without the "
                          "library's representation"))
        qs.append(question("perturbation.library_representation", "Cells per control guide vary "
                           f"{tot.max() / tot.min():.1f}-fold. Is the library's representation (plasmid or early "
                           "counts) available?", "without it, cell numbers cannot be read as fitness"))
    return facts, qs


# 4. combinations, labels and metadata -------------------------------------------------------------------------------
def split_pair(label, singles):
    """(a, b) when label is two single labels joined by one non-alphanumeric character, else None."""
    for i, ch in enumerate(label):
        if not ch.isalnum() and label[:i] in singles and label[i + 1:] in singles:
            return label[:i], label[i + 1:]
    return None


def testable_pairs(labels, unit, label, min_cells=10):
    """labels: per-cell perturbation labels (singles and pairs); a pair can be tested for interaction in a unit holding
    it and both its singles with min_cells each."""
    lab = pd.Series(np.asarray(labels, dtype=str))
    u = pd.Series(np.asarray(unit, dtype=str))
    keep = lab != ""
    lab, u = lab[keep], u[keep]
    counts = pd.crosstab(lab, u)
    names = set(counts.index)
    pairs = {p: split_pair(p, names) for p in names}
    pairs = {p: ab for p, ab in pairs.items() if ab and counts.loc[p].max() >= min_cells}
    if len(pairs) < 3:
        return [], []
    ok = {p: [str(x) for x in counts.columns if counts.at[p, x] >= min_cells and counts.at[a, x] >= min_cells
              and counts.at[b, x] >= min_cells] for p, (a, b) in pairs.items()}
    untestable = sorted(p for p, us in ok.items() if not us)
    facts = [fact("perturbation.testable_pairs", {"pairs": len(pairs), "testable": len(pairs) - len(untestable),
                                                  "untestable": untestable[:15], "by": label}, "observed",
                  f"cells per label and {label}",
                  f"a pair is testable in a {label} holding it and both its singles ({min_cells} cells each)")]
    qs = []
    if untestable:
        qs.append(question("perturbation.testable_pairs", f"{len(untestable)} of {len(pairs)} pairs have no {label} "
                           f"that holds both of their singles (e.g. {untestable[0]}). Leave them out of interaction "
                           "tests, or compare across units?", "an interaction is a claim about three groups in one "
                           "replicate"))
    return facts, qs


def _derived(table, source, other):
    """The share of rows whose `other` value is the one most rows with the same `source` value hold."""
    t = table[[source, other]].dropna().astype(str)
    t = t[t[source] != ""]
    if t.empty:
        return 0.0
    top = t.groupby(source)[other].agg(lambda s: s.value_counts().iloc[0])
    return float(top.sum() / len(t))


def label_disagreements(ref_labels, table, guide_column):
    """ref_labels: guide -> the feature reference's label; table: cell metadata with a guide column holding one guide
    per cell. A column whose value follows from the guide and agrees with the reference for most guides is a label
    column; the guides where it disagrees are reported, grouped by the two labels."""
    if not ref_labels or guide_column not in table:
        return [], []
    t = table[table[guide_column].astype(str).isin(set(ref_labels))]
    facts, qs = [], []
    for c in t.columns:
        if c == guide_column or t[c].dtype.kind in "fiu" or _derived(t, guide_column, c) < 0.95:
            continue
        # a guide's label in the column: its most common value; a tie is no one label, and says so
        per = t.groupby(guide_column)[c].agg(lambda s: " or ".join(most_common(s.astype(str))[1])
                                             or most_common(s.astype(str))[0])
        agree = [g for g, v in per.items() if v == ref_labels.get(g)]
        if len(agree) < 0.5 * len(per):
            continue
        off = {}
        for g, v in per.items():
            if v != ref_labels.get(g):
                off.setdefault(ref_labels.get(g), {}).setdefault(v, []).append(g)
        if off:
            value = {ref: {"table_labels": sorted(vs), "guides": sum(len(x) for x in vs.values()),
                           "examples": [g for x in vs.values() for g in x][:4]} for ref, vs in off.items()}
            facts.append(fact(f"perturbation.label_disagreement.{c}", value, "observed",
                              "feature reference against cell metadata", "the two files label these guides differently"))
    if facts:
        ref, v = next(iter(facts[0]["value"].items()))
        qs.append(question("perturbation.label_disagreement", f"The feature reference labels {v['guides']} guides "
                           f"'{ref}' (e.g. {v['examples'][0]}); the cell metadata label them "
                           f"{', '.join(repr(x) for x in v['table_labels'][:3])}. Which is right? For controls this "
                           "decides whether they cut.", "never pick one silently"))
    return facts, qs


GUIDE_SEPARATORS = re.compile(r"[,|;]")
NAME_PARTS = re.compile(r"[^A-Za-z0-9]+")


def _shares_names(values, guide_names):
    """Whether most values are built from the guides' name parts (each part of a value begins a part of some guide
    name: 'CEBPB-a' from 'CEBPB-a2'), as perturbation labels are and batch or sample names are not."""
    parts = {p for g in guide_names for p in NAME_PARTS.split(str(g)) if p}
    if not parts:
        return False
    ok = 0
    vals = [str(v) for v in values]
    for v in vals:
        ps = [p for p in NAME_PARTS.split(v) if p]
        if ps and all(any(q.startswith(p) for q in parts) for p in ps):
            ok += 1
    return ok >= 0.5 * max(len(vals), 1)


def cell_metadata(table, matrix_barcodes, guide_names):
    """A table read whole. When a column's values are the matrix's barcodes (at least half of the table's rows), the
    table is cell metadata: (facts, questions, the table indexed by barcode, guide columns, label columns derived from
    a guide column). Otherwise (None-valued) nothing."""
    bcs = set(matrix_barcodes)
    key = None
    for c in table.columns:
        v = table[c].astype(str).to_numpy(dtype=object)
        if len(v) and sum(x in bcs for x in v) / len(v) >= 0.5:  # set membership: a large barcode set is costly to isin
            key = c
            break
    if key is None:
        return [], [], None, [], []
    t = table.set_index(table[key].astype(str))
    known = set(guide_names)
    guide_cols, label_cols = [], []
    for c in t.columns:
        if c == key or t[c].dtype.kind in "fiu":
            continue
        uniq = pd.Series(t[c].astype(str).unique())
        parts = uniq.str.split(GUIDE_SEPARATORS).explode().str.strip()
        if len(parts) and parts.isin(known).mean() >= 0.5:
            guide_cols.append(c)
    for g in guide_cols:
        for c in t.columns:
            if c in (key, g) or c in guide_cols or t[c].dtype.kind in "fiu":
                continue
            n_c, n_g = t[c].nunique(), t[g].nunique()
            if 1 < n_c <= n_g and not t[c].astype(str).equals(t[g].astype(str)) and _derived(t, g, c) >= 0.95 \
                    and _shares_names(t[c].astype(str).unique(), known):
                label_cols.append(c)
    facts = [fact("data.cell_metadata", {"rows": int(len(t)), "matched": round(float(t.index.isin(bcs).mean()), 3),
                                         "columns": [str(c) for c in t.columns], "guide_columns": guide_cols,
                                         "labels_from_guides": label_cols}, "observed",
                  "a table keyed by the matrix's barcodes")]
    qs = []
    if label_cols:
        qs.append(question("perturbation.deposit_labels", f"The cell metadata hold perturbation labels derived from "
                           f"the guides (column '{label_cols[0]}'). Use the deposit's labels as the perturbation "
                           "unit, after checking them against the guide calls?",
                           "re-deriving labels from guide names is where parsing mistakes happen"))
    return facts, qs, t, guide_cols, label_cols


def columns_by_unit(table, cell_barcodes, unit, max_levels=50):
    """The cell metadata's columns of few values (at most max_levels), unit by unit: {column: {unit: {value: cells}}},
    from each cell's barcode (its first row in the table) and its unit. A design fact read from a column (its node's
    `column`) is checked against it (checks._columns)."""
    t = table[~table.index.duplicated()]
    pos = t.index.get_indexer([str(b) for b in cell_barcodes])
    found = pos >= 0
    unit = np.asarray(unit).astype(str)
    out = {}
    for c in t.columns:
        v = t[c].astype(str).to_numpy(dtype=object)
        if t[c].nunique(dropna=True) > max_levels:
            continue
        per = pd.DataFrame({"unit": unit[found], "value": v[pos[found]]})
        out[str(c)] = {u: {str(k): int(n) for k, n in g["value"].value_counts().items()}
                       for u, g in per.groupby("unit")}
    if not out:
        return []
    return [fact("data.cell_metadata_by_unit", out, "observed",
                 f"the cell metadata's columns of at most {max_levels} values, by each cell's unit")]


def aggregation_order(table, ids_column, paired_calls):
    """table: a Cell Ranger aggregation table, its rows in suffix order (row 1 is '-1'); paired_calls: [{path,
    suffix}] measured by barcode overlap. Reports the suffix of each channel and whether that order differs from the
    channels' sorted names."""
    ids = table[ids_column].astype(str).tolist()
    by_row = {str(i + 1): x for i, x in enumerate(ids)}
    measured = {}
    for c in paired_calls:
        hits = [x for x in ids if x in os.path.basename(c["path"])]
        if len(hits) == 1 and c.get("suffix"):
            measured[hits[0]] = c["suffix"]
    if not measured:
        return [], []
    agree = all(by_row.get(s) == x for x, s in measured.items())
    facts = [fact("data.aggregation_order", {"suffix_to_channel": by_row, "agrees_with_calls": agree}, "observed",
                  "aggregation table against the per-channel calls' barcodes")]
    if agree and ids != sorted(ids):
        facts[0]["note"] = (f"suffixes follow the aggregation table's order ({', '.join(ids)}), not the channels' "
                            "sorted names: matching channel files by name order would mislabel cells")
    elif not agree:
        facts[0]["note"] = "the aggregation table's order does not match the barcodes; the barcodes decide"
    return facts, []


def factor_confounding(unit_factors):
    """unit_factors: units x factors (one value per unit). Pairs of factors where a level of one occurs under a
    single level of the other, which still has other levels; factors that name every unit, or never vary, are left
    out."""
    f = unit_factors.astype(str)
    cols = [c for c in f.columns if 1 < f[c].nunique() < len(f)]
    found = []
    for a in cols:
        for b in cols:
            if a == b:
                continue
            for level, rows in f.groupby(a):
                under = rows[b].unique()
                if len(under) == 1 and f[b].nunique() > 1 and len(rows) < len(f):
                    found.append({"factor": a, "level": str(level), "only_under": b, "of_that": str(under[0])})
    same = [(a, b) for i, a in enumerate(cols) for b in cols[i + 1:]
            if pd.crosstab(f[a], f[b]).astype(bool).sum(1).eq(1).all()
            and pd.crosstab(f[a], f[b]).astype(bool).sum(0).eq(1).all()]
    if not found:
        return [], []
    facts = [fact("units.confounded_factors", {"same_split": [list(p) for p in same], "nested": found[:12]},
                  "observed", "factors per unit",
                  "these factors cannot be separated where a level occurs under one level of another")]
    return facts, [question("units.confounded_factors", "Some factors cannot be separated across units (e.g. "
                            f"{found[0]['factor']} = {found[0]['level']} only under {found[0]['only_under']} = "
                            f"{found[0]['of_that']}). Compare within the shared level, or relative to other targets?",
                            "a batch confounded with a condition or library looks like an effect")]


def characteristic_per_matrix(geo, matrices, samples_with_matrix=None):
    """A GEO characteristic with exactly as many levels as the deposit has expression matrices: each matrix likely
    holds one level, so it cannot be told from the matrix's own capture batch. samples_with_matrix: the GEO samples
    the expression matrices are named by, when they all are; only their characteristics count then."""
    if not geo or matrices < 2:
        return [], []
    by = {}
    for gsm, s in geo["samples"].items():
        if samples_with_matrix and gsm not in samples_with_matrix:
            continue
        for k, v in s["characteristics"].items():
            by.setdefault(k, set()).add(v[0])
    hits = {k: sorted(v) for k, v in by.items() if len(v) == matrices}
    if not hits:
        return [], []
    k, v = next(iter(hits.items()))
    return [fact("experiment.characteristic_per_matrix", hits, "inferred", "GEO characteristics and matrices",
                 "as many levels as matrices: each matrix likely holds one level, which its capture batch then "
                 "shares")], [
        question("experiment.characteristic_per_matrix", f"'{k}' has {len(v)} levels ({', '.join(v[:3])}) and the "
                 f"deposit {matrices} matrices. Does each matrix hold one level? Then compare within matrices, "
                 "relative to the other targets, not raw expression between them.",
                 "a condition captured apart is confounded with its capture")]


def samples_differ(geo, samples_with_matrix=None):
    """The GEO characteristics the deposit's samples differ in, each with its levels: {characteristic: [levels]} for
    each one with two or more levels (docs/adr/0045, D7). Measured, never read as an axis: the form asks the user
    whether each is a condition, a unit, a batch or not compared. samples_with_matrix: as characteristic_per_matrix."""
    if not geo:
        return [], []
    by = {}
    for gsm, s in geo["samples"].items():
        if samples_with_matrix and gsm not in samples_with_matrix:
            continue
        for k, v in s["characteristics"].items():
            by.setdefault(k, set()).add(v[0])
    differ = {k: sorted(v) for k, v in by.items() if len(v) >= 2}
    if not differ:
        return [], []
    return [fact("experiment.samples_differ", differ, "observed", "GEO characteristics of the samples")], []


def library_against_metadata(guide_names, gene_names, geo):
    """The genes the guide names contain against the genes the GEO text names (both as words that are genes of the
    matrix): a library that differs from what the metadata describe."""
    if not geo or not guide_names:
        return [], []
    genes = set(gene_names)
    in_guides = {p for g in guide_names for p in NAME_PARTS.split(str(g)) if p in genes}
    text = " ".join(geo["series"].get("summary", []) + geo["series"].get("overall_design", [])
                    + [geo["series"].get("title", "")])
    in_text = {p for p in NAME_PARTS.split(text) if p in genes and len(p) > 2}
    if len(in_guides) < 3 or len(in_text) < 3:
        return [], []
    only_guides, only_text = sorted(in_guides - in_text), sorted(in_text - in_guides)
    if not only_text and not only_guides:
        return [], []
    return [fact("perturbation.library_against_metadata", {"genes_in_guide_names": len(in_guides),
                                                           "named_in_metadata_not_in_guides": only_text[:12],
                                                           "in_guides_not_named": only_guides[:12]},
                 "observed", "guide names and GEO text, as genes of the matrix")], [
        question("perturbation.library_against_metadata", "The guides and the GEO text name different genes ("
                 + "; ".join(x for x in (f"only in the metadata: {', '.join(only_text[:4])}" if only_text else "",
                                         f"only in the guides: {', '.join(only_guides[:4])}" if only_guides else "")
                             if x)
                 + "). Is the library what the metadata describe (missing guides, truncated names)?",
                 "a target missing from the data cannot be analysed; confirm the library")]


def reading_questions(free_text_files, annotation_columns, hashtag_groups):
    """The questions only reading (the methods, a README, the user) can settle."""
    qs = [question("experiment.pooled_populations", "Were cells of another population pooled into any sample before "
                   "sequencing (wild-type cells, a spike-in line, another genotype)? The methods usually say; a "
                   "marker gene per group can confirm it.", "a pooled-in population sits silently inside groups"),
          question("experiment.outside_data", "Does the analysis depend on data outside this deposit (a reference "
                   "screen, a gene list, a paper's table)? List them before planning.",
                   "an outside dependency missing from the plan blocks a whole question")]
    if free_text_files:
        qs.append(question("data.free_text", "The deposit includes free-text files ("
                           + ", ".join(os.path.basename(f) for f in free_text_files[:3])
                           + "). Check their sample, hashtag and library descriptions against the tables.",
                           "a README can disagree with the files; the files decide, and the user confirms"))
    if annotation_columns:
        qs.append(question("data.annotations", "The deposit carries cell annotations ("
                           + ", ".join(str(c) for c in annotation_columns[:4])
                           + "). Are they the paper's? Compare before using them as states.",
                           "a deposit's labels can differ from the published ones"))
    if hashtag_groups:
        qs.append(question("units.hashtag_map", "Map each hashtag group (per unit, with its cells) to an animal, "
                           "sample or condition in the metadata, and list any group the metadata do not account for.",
                           "unlisted groups are units nobody can name"))
    return qs
