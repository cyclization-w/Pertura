"""The cells overview, written by the screen object's build: what the cells' quality looks like, measured and drawn by
code, so that the agent proposes the quality rule (the design's data.qc) from it and the user confirms it after the
build, seeing it. It decides nothing: no cut-off is applied here.

What it holds, in <screen object>/cells_overview/:
- measures.tsv.gz: each cell's four measures (pertura.quality: UMIs, genes detected, the mitochondrial and the
  ribosomal protein shares, as shares of its counts);
- clusters.tsv: every cell whose unit was told (with a guide or not), its cluster (C01, C02, ... by size) and its
  place on a UMAP. The clusters are Leiden clusters of the log-normalised highly variable genes (pertura.states'
  embedding, scanpy's defaults), a coarse first look at the cell types and the low-quality cells, as published screens
  cluster every cell that passed their quality control before they leave out other cell types and keep the cells with
  one guide for their tests;
- cluster_summary.tsv: per cluster its cells, its shares without a guide and with several, its median UMIs, genes and
  mitochondrial share, its share over a tenth of mitochondrial counts, and its markers (scanpy's Wilcoxon against the
  other clusters, on up to MARKER_CELLS cells of each; mitochondrial and ribosomal protein genes left out, as they
  name no cell type);
- cuts.tsv: per sample, the cells with a unit that each round cut of each measure keeps (CUTS), for the agent's
  proposal and the user's look; a rule's own cuts are counted by rule_counts;
- deposit_object_ranges.json, where the deposit holds a processed object: its cells and each numeric column's
  minimum, median and maximum, which may show the rule its makers applied (the agent reads which column is which);
- note.json: why there are no clusters, where there are none;
- figure.png and figure.pdf: the clusters on the UMAP, the mitochondrial share and the UMIs on it, each cluster's
  share without a guide, and each sample's histograms of log UMIs, log genes and the mitochondrial share (with a
  rule's cuts drawn, by draw(..., rule)).
"""
import json
import os

import numpy as np
import pandas as pd

from .quality import UNNAMED  # genes that name no cell type, left out of the markers shown

FOLDER = "cells_overview"
CUTS = {"pct_mito": (0.05, 0.08, 0.1, 0.15, 0.2), "n_genes": (200, 500, 1000), "n_counts": (500, 1000, 2000)}
CUT_SIDE = {"pct_mito": "max", "pct_ribo": "max", "n_genes": "min", "n_counts": "min"}
MARKER_CELLS = 1000  # cells per cluster for its markers: enough to name a cluster, at a fraction of the time
MARKERS = 10
MIN_CELLS_TO_CLUSTER = 50  # fewer cells give no clusters worth naming
HIGH_MITO = 0.1  # the summary's column of cells over a tenth of mitochondrial counts (a description, not a cut)
NO_GUIDE, SEVERAL = ("none",), ("multi", "ambiguous", "two sets", "incomplete")
SUMMARY_COLUMNS = ["cluster", "cells", "share_without_guide", "share_several_guides", "median_umis", "median_genes",
                   "median_pct_mito", "share_pct_mito_over_10", "markers", "name"]
RED = "#B00000"


def _with_unit(cells):
    """The cells whose unit the build could tell (pertura.question_modules.data.UNASSIGNED marks the others)."""
    from .question_modules.data import UNASSIGNED
    return ~cells["unit"].astype(str).str.endswith(UNASSIGNED).to_numpy()


def write(screen_dir, adata, deposit_objects=(), seed=0):
    """Measure, cluster and draw the cells of a built screen object (adata: its counts, obs its cells.tsv). Returns
    what the build's reply carries (reply)."""
    import scipy.sparse as sp
    from . import quality, states
    out = os.path.join(screen_dir, FOLDER)
    os.makedirs(out, exist_ok=True)
    cells = adata.obs
    X = sp.csr_matrix(adata.X)
    genes = np.asarray(adata.var_names).astype(str)
    m = quality.measures_of(X, genes)
    m.insert(0, "barcode", np.asarray(adata.obs_names).astype(str))
    m.to_csv(os.path.join(out, "measures.tsv.gz"), sep="\t", index=False)
    idx = np.flatnonzero(_with_unit(cells))
    try:
        import scanpy as sc
    except ImportError:  # the clusters need scanpy; the measures, cuts and histograms do not
        sc = None
    note = None
    if sc is None:
        note = "no clusters: scanpy is not installed here"
    elif len(idx) < MIN_CELLS_TO_CLUSTER:
        note = f"no clusters: {len(idx)} cells with a unit, fewer than {MIN_CELLS_TO_CLUSTER}"
    if note is None:
        try:
            a, h = states.embed(X[idx], genes, seed=seed)
            lab = np.array(["C" + s[1:] for s in states.leiden_states(h, 1.0, seed)])
            sc.tl.umap(h, random_state=seed)
            xy = h.obsm["X_umap"]
            clusters = pd.DataFrame({"barcode": m["barcode"].to_numpy()[idx], "cluster": lab,
                                     "umap_1": xy[:, 0], "umap_2": xy[:, 1]})
            summary = _summary(cells.iloc[idx], m.iloc[idx], lab, _markers(a, lab, seed))
        except Exception as e:  # the overview decides nothing: without its clusters the build still stands
            note = f"no clusters: {type(e).__name__}: {str(e)[:300]}"
    if note is not None:
        clusters = pd.DataFrame(columns=["barcode", "cluster", "umap_1", "umap_2"])
        summary = pd.DataFrame(columns=SUMMARY_COLUMNS)
    clusters.to_csv(os.path.join(out, "clusters.tsv"), sep="\t", index=False)
    summary.to_csv(os.path.join(out, "cluster_summary.tsv"), sep="\t", index=False)
    _cuts(cells.iloc[idx], m.iloc[idx]).to_csv(os.path.join(out, "cuts.tsv"), sep="\t", index=False)
    ranges = object_ranges(deposit_objects[0]) if deposit_objects else None
    with open(os.path.join(out, "deposit_object_ranges.json"), "w", encoding="utf-8") as fh:
        json.dump(ranges, fh, indent=1)
    with open(os.path.join(out, "note.json"), "w", encoding="utf-8") as fh:
        json.dump({"note": note, "genes_counted": quality.genes_counted(genes)}, fh)
    draw(screen_dir)
    return reply(screen_dir)


def _markers(a, lab, seed):
    """Each cluster's top MARKERS genes (Wilcoxon, each cluster against the rest) on up to MARKER_CELLS of its cells,
    the genes that name no cell type left out."""
    import re
    import scanpy as sc
    rng = np.random.default_rng(seed)
    take = np.sort(np.concatenate([rng.permutation(np.flatnonzero(lab == c))[:MARKER_CELLS]
                                   for c in sorted(set(lab))]))
    sub = a[take].copy()
    sub.obs["cluster"] = pd.Categorical(lab[take])
    if sub.obs["cluster"].nunique() < 2:
        return {}
    sc.tl.rank_genes_groups(sub, "cluster", method="wilcoxon", n_genes=MARKERS * 4)
    out = {}
    for c in sorted(set(lab)):
        names = sc.get.rank_genes_groups_df(sub, c)["names"].astype(str)
        out[c] = [g for g in names if not re.match(UNNAMED, g)][:MARKERS]
    return out


def _summary(cells, m, lab, markers):
    lab_ = cells["label"].astype(str).to_numpy()
    rows = []
    for c in sorted(set(lab)):
        k = lab == c
        mito = m["pct_mito"].to_numpy()[k]
        rows.append({"cluster": c, "cells": int(k.sum()),
                     "share_without_guide": round(float(np.isin(lab_[k], NO_GUIDE).mean()), 3),
                     "share_several_guides": round(float(np.isin(lab_[k], SEVERAL).mean()), 3),
                     "median_umis": float(np.median(m["n_counts"].to_numpy()[k])),
                     "median_genes": float(np.median(m["n_genes"].to_numpy()[k])),
                     "median_pct_mito": round(float(np.median(mito)), 4),
                     "share_pct_mito_over_10": round(float((mito > HIGH_MITO).mean()), 3),
                     "markers": " ".join(markers.get(c, [])), "name": ""})
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _cuts(cells, m):
    samples = cells["sample"].astype(str).to_numpy()
    rows = []
    for measure, values in CUTS.items():
        x = m[measure].to_numpy()
        for cut in values:
            ok = x <= cut if CUT_SIDE[measure] == "max" else x >= cut
            for s in sorted(set(samples)):
                rows.append({"measure": measure, "cut": cut, "side": CUT_SIDE[measure], "sample": s,
                             "cells": int((samples == s).sum()), "cells_left": int((ok & (samples == s)).sum())})
    return pd.DataFrame(rows, columns=["measure", "cut", "side", "sample", "cells", "cells_left"])


def object_ranges(path):
    """A processed object's cells and each numeric cell column's minimum, median and maximum."""
    import anndata as ad
    try:
        obs = ad.read_h5ad(path, backed="r").obs
    except Exception as e:  # an object Pertura cannot read is said, not hidden
        return {"file": os.path.basename(path), "error": f"{type(e).__name__}: {e}"[:300]}
    cols = {}
    for c in obs.columns:
        if obs[c].dtype.kind in "fiu":
            v = pd.to_numeric(obs[c], errors="coerce").dropna()
            if len(v):
                cols[str(c)] = {"min": float(v.min()), "median": float(v.median()), "max": float(v.max())}
    return {"file": os.path.basename(path), "cells": int(len(obs)), "columns": cols}


class _MeasuredCells:
    """The few things quality.failures reads of a screen, from the cell table alone."""

    def __init__(self, cells):
        self.label = cells["label"].astype(str).to_numpy()
        self.capture = cells["unit"].astype(str).to_numpy()
        self.sample = cells["sample"].astype(str).to_numpy()
        self.unit_call = (cells["unit_call"].astype(str).to_numpy() if "unit_call" in cells
                          else np.full(len(cells), "", dtype=object))
        self.barcodes = cells["barcode"].astype(str).to_numpy()
        self._unassigned = ~_with_unit(cells)

    def unassigned(self):
        return self._unassigned


def rule_counts(screen_dir, rule):
    """What a proposed quality rule (data.qc's value) leaves, per sample: the cells with a unit, those it keeps, and
    those each of its filters leaves out (a cell counted under its first failing filter), from the cells overview's
    measures. Raises ValueError where the overview is missing."""
    from . import quality
    out = os.path.join(screen_dir, FOLDER)
    if not os.path.exists(os.path.join(out, "measures.tsv.gz")):
        raise ValueError("no cells overview in this screen object: it is written by a build of this release")
    cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", keep_default_na=False)
    m = pd.read_csv(os.path.join(out, "measures.tsv.gz"), sep="\t").set_index("barcode").reindex(cells["barcode"])
    reason, _ = quality.failures(_MeasuredCells(cells), rule, table=m.reset_index(drop=True))
    with_unit = _with_unit(cells)
    rows = []
    for s in sorted(set(cells["sample"].astype(str))):
        k = (cells["sample"].astype(str) == s).to_numpy() & with_unit
        row = {"sample": s, "cells_with_a_unit": int(k.sum()), "kept": int((k & (reason == "")).sum())}
        for r, n in pd.Series(reason[k & (reason != "")]).value_counts().items():
            row[f"left out: {r}"] = int(n)
        rows.append(row)
    return pd.DataFrame(rows).fillna(0)


def confirmed_on_this_build(screen_dir, dates):
    """Whether the cells' quality rule was confirmed on the build in screen_dir: one of its confirmations' dates
    (local times, as the design service writes them) is not before the build's. Each build numbers its clusters
    anew, so a rule confirmed on another build's overview is asked again on this one."""
    with open(os.path.join(screen_dir, "screen.json"), encoding="utf-8") as fh:
        built = str(json.load(fh).get("built") or "")[:19]
    return max((str(d or "")[:19] for d in dates), default="") >= built


def reply(screen_dir):
    """The overview as a reply carries it (the workbench reads it there): the folder, the clusters' summary rows,
    the cuts' rows, the deposited object's ranges, the figure and why there are no clusters, if there are none."""
    out = os.path.join(screen_dir, FOLDER)
    summary = pd.read_csv(os.path.join(out, "cluster_summary.tsv"), sep="\t", keep_default_na=False)
    cuts = pd.read_csv(os.path.join(out, "cuts.tsv"), sep="\t")
    with open(os.path.join(out, "deposit_object_ranges.json"), encoding="utf-8") as fh:
        ranges = json.load(fh)
    try:
        with open(os.path.join(out, "note.json"), encoding="utf-8") as fh:
            kept = json.load(fh)
    except (OSError, ValueError):
        kept = {}
    note, counted = kept.get("note"), kept.get("genes_counted")
    from .quality import none_counted
    none = none_counted(counted) if counted else None
    try:
        with open(os.path.join(out, "figure.checks.json"), encoding="utf-8") as fh:
            issues = json.load(fh).get("layout_issues") or []
    except (OSError, ValueError):
        issues = []
    return {"folder": out, "clusters": _rows(summary), "cuts": _rows(cuts), "deposit_object_ranges": ranges,
            "figure": os.path.join(out, "figure.png"), **({"note": note} if note else {}),
            **({"genes_each_share_counts": counted} if counted else {}), **({"no_gene_counted": none} if none else {}),
            **({"figure_layout_issues": issues} if issues else {})}


def _rows(table):
    """A table's rows as plain JSON values, missing values as null."""
    def plain(x):
        x = x.item() if hasattr(x, "item") else x
        return None if isinstance(x, float) and x != x else x
    return [{k: plain(v) for k, v in row.items()} for row in table.to_dict("records")]


def _cuts_of(rule):
    """A rule's fixed cuts by measure: {measure: [(side, value)]} (its MAD filters move by sample; their counts say
    what they leave out)."""
    out = {}
    for f in (rule or {}).get("filters") or []:
        if isinstance(f, dict) and f.get("metric") in CUT_SIDE and not f.get("table"):
            for side in ("min", "max"):
                if side in f:
                    out.setdefault(f["metric"], []).append((side, float(f[side])))
    return out


def _dropped(rule):
    out = set()
    for f in (rule or {}).get("filters") or []:
        if isinstance(f, dict) and f.get("metric") == "cluster" and "drop" in f:
            out |= {str(x) for x in np.atleast_1d(f["drop"])}
    return out


def draw(screen_dir, rule=None, name="figure"):
    """The overview's figure from its tables, composed of the field's charts (charts.py, laid out and checked by
    figure_series.compose): a, the cells on a UMAP by cluster, each named by its markers; b and c, the same UMAP by
    mitochondrial share and by UMIs; d, each cluster's share of cells without a guide; e-g, each sample's
    distributions of UMIs, genes and mitochondrial share. With a rule, its fixed cuts are drawn on the distributions
    and the clusters it leaves out are named so and drawn in light grey (name: the files' stem). The layout checks'
    issues are kept beside it (<name>.checks.json). Returns the PNG's path."""
    from .question_modules import charts, figure_series
    out = os.path.join(screen_dir, FOLDER)
    cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", keep_default_na=False)
    m = pd.read_csv(os.path.join(out, "measures.tsv.gz"), sep="\t").set_index("barcode")
    cl = pd.read_csv(os.path.join(out, "clusters.tsv"), sep="\t")
    summary = pd.read_csv(os.path.join(out, "cluster_summary.tsv"), sep="\t", keep_default_na=False)
    shown = cl["barcode"].astype(str) if len(cl) else cells.loc[_with_unit(cells), "barcode"].astype(str)
    m = m.reindex(shown)
    samples = cells.set_index("barcode")["sample"].astype(str).reindex(shown).to_numpy()
    dropped, cuts = _dropped(rule), _cuts_of(rule)
    rows = []
    if len(cl):
        lab = cl["cluster"].astype(str).to_numpy()
        xy = cl[["umap_1", "umap_2"]].to_numpy(dtype=float)
        markers = dict(zip(summary["cluster"].astype(str), summary["markers"].astype(str)))
        names = {c: " ".join([c, *markers.get(c, "").split()[:2]]) + (" (left out)" if c in dropped else "")
                 for c in sorted(set(lab))}
        rows.append((70, [(1.5, lambda p: charts.embedding_states(xy, lab, names, None, "", named_on_map=0, fig=p)),
                          (0.75, lambda p: charts.embedding_values(xy, m["pct_mito"].to_numpy(float), None,
                                                                   "Mitochondrial share", fig=p)),
                          (0.75, lambda p: charts.embedding_values(xy, m["n_counts"].to_numpy(float), None, "UMIs",
                                                                   log=True, fig=p))]))
    second = []
    if len(summary):
        share = summary.assign(target=summary["cluster"].astype(str),
                               share_without_guide=summary["share_without_guide"].astype(float))
        second.append((1.1, lambda p: charts.ranked_targets(share, "share_without_guide", None,
                                                            "Share without a guide", "", muted=(dropped, "Left out"), fig=p)))
    table = pd.DataFrame({"sample": samples, **{c: m[c].to_numpy(float) for c in ("n_counts", "n_genes",
                                                                                "pct_mito")}})
    for k, (col, label, log) in enumerate((("n_counts", "UMIs per cell", True), ("n_genes", "Genes per cell", True),
                                           ("pct_mito", "Mitochondrial share", False))):
        second.append((1.0 if k == 2 else 0.85, lambda p, col=col, label=label, log=log, k=k: charts.distributions(
            table, col, "sample", None, label, log=log, cuts=[x for _, x in cuts.get(col, [])], key=k == 2, fig=p)))
    rows.append((52, second))
    title = "The cells before quality control: clusters, quality measures and each sample's distributions"
    path = os.path.join(out, f"{name}.png")
    issues, _ = figure_series.compose(rows, path, title)
    with open(os.path.join(out, f"{name}.checks.json"), "w", encoding="utf-8") as fh:
        json.dump({"layout_issues": issues}, fh)
    return path


def _bare(ax, xlabel, ylabel):
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
