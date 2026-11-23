"""Quality control of a screen's cells: each cell's measures, and the rule the user confirms in the design (data.qc).

Pertura measures four things of every cell: its UMIs (n_counts), its genes detected (n_genes), and the shares of its
counts from mitochondrial and from ribosomal protein genes (pct_mito, pct_ribo; the genes named mt- and rps or rpl, as
scanpy's tutorials name them). The rule is a list of filters, none by default:
- {"metric": <a measure>, "min": x} or {"max": y}: a fixed threshold (a paper's, or the user's habit);
- {"metric": <a measure>, "mads": k}: within each sample (one sequencing library), the cells more than k median
  absolute deviations from its median (below it for n_counts and n_genes, on the log scale; above it for the shares;
  "side" low, high or both says otherwise);
- {"metric": <a name>, "table": <a TSV with columns barcode and that name>} with min, max, mads or "drop" (values to
  drop): a measure of the user's or the agent's own, such as a doublet score from scanpy's scrublet.
The rule may carry "reasons", {a filter's words (describe_filter): why it is there}, which the form and the report
give beside it. By default the cells whose unit could not be told (no value in the unit column) are left out too;
"keep_cells_without_a_unit": true keeps them. Cell cycle is a state, not quality (C.cell_cycle).
A cell that fails is labelled FAILED with its reason, and every module and block leaves it out.
"""
import re

import numpy as np
import pandas as pd

MEASURES = ("n_counts", "n_genes", "pct_mito", "pct_ribo")
FAILED = "failed QC"
LOG_SCALE = ("n_counts", "n_genes")  # a MAD filter reads these on log1p, as their spread is multiplicative
KEYS = {"metric", "min", "max", "mads", "side", "table", "drop"}


def measures(screen):
    """Each cell's four measures, as a table in the screen's cell order."""
    return measures_of(screen.X, screen.genes, screen.libsize)


RECEPTOR_SEGMENTS = re.compile(r"^((tr[abdg]|ig[hkl])([vdj]\d|c\d*$)|tcrg-[vjc]\d)", re.IGNORECASE)


def receptor_genes(names):
    """Which of the gene names are T- and B-cell receptor segments (TRAV12-1, Trbv3, TRAC, Tcrg-V4, IGHV1-2, Igkc): a
    clone's own genes, left out of profiles and clustering (the knowledge base's PF4; Heumos et al. 2023; Sundell et
    al. 2023)."""
    return np.array([bool(RECEPTOR_SEGMENTS.match(str(g))) for g in names], dtype=bool)


def variable_genes(a, **kw):
    """scanpy's highly variable genes (pp.highly_variable_genes, its arguments as given), the receptor segments among
    them left out (receptor_genes) and named in a.uns["receptor_segments_left_out"], for the run to say."""
    import scanpy as sc
    sc.pp.highly_variable_genes(a, **kw)
    out = receptor_genes(a.var_names) & a.var["highly_variable"].to_numpy(dtype=bool)
    a.var.loc[out, "highly_variable"] = False
    a.uns["receptor_segments_left_out"] = sorted(map(str, a.var_names[out]))


UNNAMED = r"^(MT-|mt-|MTRNR|Mtrnr|RP[SL]\d|Rp[sl]\d|RPLP|Rplp)"  # genes that name no cell type: out of marker lists
PREFIXES = {"pct_mito": "mt-", "pct_ribo": "rps or rpl"}


def mito_ribo(genes):
    """Which genes pct_mito and pct_ribo count: names starting mt- (mitochondrial) and rps or rpl (ribosomal protein
    genes), in any case, as scanpy's tutorials name them; two boolean masks over the genes."""
    low = np.char.lower(np.asarray(genes).astype(str))
    return np.char.startswith(low, "mt-"), np.char.startswith(low, "rps") | np.char.startswith(low, "rpl")


def genes_counted(genes):
    """The genes behind pct_mito and pct_ribo, said beside the shares (docs/adr/0041, step 6): how many, and the
    first few."""
    names = np.asarray(genes).astype(str)
    return {k: {"genes": int(m.sum()), "such as": names[m][:4].tolist()}
            for k, m in zip(("pct_mito", "pct_ribo"), mito_ribo(names))}


def none_counted(counted):
    """The words where a share counts no gene, which gives every cell a share of 0 that reads as clean cells; None
    where each counts some."""
    return "; ".join(f"{k} counts no gene: no gene's name starts with {PREFIXES[k]}, so every cell's {k} is 0 and a "
                     "filter on it removes nothing (the genes may be named by their IDs)"
                     for k, v in counted.items() if not v["genes"]) or None


def measures_of(X, genes, libsize=None):
    """The four measures of each row of a counts matrix (cells x genes) whose columns are the genes named."""
    import scipy.sparse as sp
    X = sp.csr_matrix(X)
    mito, ribo = mito_ribo(genes)
    total = np.asarray(X.sum(1)).ravel().astype(float) if libsize is None else np.asarray(libsize, float)
    lib = np.maximum(total, 1.0)

    def share(mask):
        idx = np.flatnonzero(mask)
        return np.asarray(X[:, idx].sum(1)).ravel() / lib if len(idx) else np.zeros(len(lib))
    return pd.DataFrame({"n_counts": total,
                         "n_genes": np.asarray((X > 0).sum(1)).ravel().astype(float),
                         "pct_mito": share(mito), "pct_ribo": share(ribo)})


def validate(rule):
    """The problems of a proposed rule, in words; [] where it is one Pertura can apply."""
    if rule in (None, {}):
        return []
    if not isinstance(rule, dict):
        return [f"data.qc: {rule!r} is not a rule; give {{filters: [...], keep_cells_without_a_unit: false}}"]
    problems = [f"data.qc: {k!r} is not a key of the rule (filters, keep_cells_without_a_unit, reasons)"
                for k in rule if k not in ("filters", "keep_cells_without_a_unit", "reasons")]
    if "reasons" in rule and not isinstance(rule["reasons"], dict):
        problems.append("data.qc: reasons is {a filter's words: why}")
    filters = rule.get("filters") or []
    if not isinstance(filters, list):
        return problems + ["data.qc: filters is a list of filters"]
    for i, f in enumerate(filters):
        where = f"data.qc filter {i + 1}"
        if not isinstance(f, dict) or not f.get("metric"):
            problems.append(f"{where}: each filter names its metric ({', '.join(MEASURES)}, or a column with table)")
            continue
        extra = set(f) - KEYS
        if extra:
            problems.append(f"{where}: {', '.join(sorted(extra))} is not a key of a filter ({', '.join(sorted(KEYS))})")
        if f["metric"] not in MEASURES and not f.get("table"):
            problems.append(f"{where}: {f['metric']!r} is none of Pertura's measures ({', '.join(MEASURES)}); a measure "
                            "of your own comes with table, a TSV of barcode and that column")
        if not any(k in f for k in ("min", "max", "mads", "drop")):
            problems.append(f"{where}: give min, max, mads or drop")
        for k in ("min", "max", "mads"):
            if k in f and (isinstance(f[k], bool) or not isinstance(f[k], (int, float))):
                problems.append(f"{where}: {k} is a number, not {f[k]!r}")
        if "mads" in f and isinstance(f["mads"], (int, float)) and f["mads"] <= 0:
            problems.append(f"{where}: mads is a positive number")
        if f.get("side") not in (None, "low", "high", "both"):
            problems.append(f"{where}: side is low, high or both")
    return problems


def _values(screen, f, table):
    if f["metric"] in MEASURES and not f.get("table"):
        return table[f["metric"]].to_numpy(dtype=float)
    t = pd.read_csv(f["table"], sep="\t")
    if "barcode" not in t or f["metric"] not in t:
        raise ValueError(f"data.qc: {f['table']} holds no columns barcode and {f['metric']}")
    s = t.set_index(t["barcode"].astype(str))[f["metric"]]
    return s.reindex(np.asarray(screen.barcodes).astype(str)).to_numpy()


def _outside_mads(x, groups, k, side):
    out = np.zeros(len(x), bool)
    for g in pd.unique(groups):
        m = (groups == g) & np.isfinite(x)
        if not m.any():
            continue
        med = np.median(x[m])
        mad = np.median(np.abs(x[m] - med))
        low, high = x < med - k * mad, x > med + k * mad
        out |= m & ({"low": low, "high": high}.get(side, low | high))
    return out


def failures(screen, rule=None, table=None):
    """Per cell, why it fails the rule ('' where it passes), and the table of the four measures (None where no filter
    needed them; table: the measures already at hand, in the screen's cell order). The cells whose unit could not be
    told fail unless the rule keeps them."""
    rule = rule or {}
    reason = np.full(len(screen.label), "", dtype=object)
    if not rule.get("keep_cells_without_a_unit"):
        reason[screen.unassigned()] = "no unit"
    filters = rule.get("filters") or []
    if table is None and any(f.get("metric") in MEASURES and not f.get("table") for f in filters):
        table = measures(screen)
    for f in filters:
        x = _values(screen, f, table)
        bad = np.zeros(len(reason), bool)
        if "min" in f:
            bad |= x < f["min"]
        if "max" in f:
            bad |= x > f["max"]
        if "drop" in f:
            bad |= np.isin(np.asarray(x).astype(str), [str(v) for v in np.atleast_1d(f["drop"])])
        if "mads" in f:
            side = f.get("side") or ("low" if f["metric"] in LOG_SCALE else "high")
            xv = np.log1p(np.asarray(x, float)) if f["metric"] in LOG_SCALE else np.asarray(x, float)
            bad |= _outside_mads(xv, _samples_of(screen), float(f["mads"]), side)
        reason[bad & (reason == "")] = describe_filter(f)
    return reason.astype(str), table


def _samples_of(screen):
    """Each cell's sample (its sequencing library), the group a filter by median absolute deviations is read in;
    the unit where the screen has no samples."""
    sample = np.asarray(getattr(screen, "sample", [])).astype(str)
    return sample if len(sample) == len(screen.label) and (sample != "").all() else np.asarray(screen.capture)


def describe_filter(f):
    """A filter in a few words."""
    parts = []
    if "min" in f:
        parts.append(f"{f['metric']} below {f['min']}")
    if "max" in f:
        parts.append(f"{f['metric']} above {f['max']}")
    if "mads" in f:
        side = f.get("side") or ("low" if f["metric"] in LOG_SCALE else "high")
        parts.append(f"{f['metric']} more than {f['mads']} MADs {'from' if side == 'both' else side + ' of'} its "
                     "capture's median")
    if "drop" in f:
        parts.append(f"{f['metric']} in {', '.join(map(str, np.atleast_1d(f['drop'])))}")
    # the table by its folder and name, not the server's whole path (GSE203592's report printed /home/...)
    return "; ".join(parts) + (f" ({'/'.join(str(f['table']).replace(chr(92), '/').split('/')[-2:])})"
                               if f.get("table") else "")


def describe(rule, unit_column=None):
    """The rule in words, for the design form and the report's methods. The cells whose unit could not be told are
    named where the design names a unit column (units.unit_column): only there can a cell lack a unit."""
    rule = rule or {}
    filters = [describe_filter(f) for f in rule.get("filters") or [] if isinstance(f, dict) and f.get("metric")]
    text = ("cells with " + ", or ".join(filters) + " are left out") if filters else "no filter on the cells' measures"
    if not unit_column:
        return text
    return text + ("; the cells whose unit could not be told are kept" if rule.get("keep_cells_without_a_unit")
                   else f"; the cells whose unit could not be told (no value in the column '{unit_column}') are left "
                        "out")


def by_capture(screen, table=None):
    """Per capture: its cells, the medians and 5th and 95th percentiles of the four measures, and the cells left out
    by each reason."""
    table = measures(screen) if table is None else table
    t = table.assign(capture=np.asarray(screen.capture), reason=np.asarray(screen.qc_reason))
    rows = []
    for c, g in t.groupby("capture"):
        row = {"capture": c, "cells": len(g)}
        for m in MEASURES:
            q = np.nanpercentile(g[m], [5, 50, 95])
            row.update({f"{m} p5": float(q[0]), f"{m} median": float(q[1]), f"{m} p95": float(q[2])})
        for r, n in g["reason"][g["reason"] != ""].value_counts().items():
            row[f"left out: {r}"] = int(n)
        rows.append(row)
    return pd.DataFrame(rows).fillna(0)
