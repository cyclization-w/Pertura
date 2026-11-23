"""The screen object: one verified model of the experiment, built from the deposit and its confirmed design
(docs/adr/0020, the first part of the core). Every analysis module reads it instead of re-reading the deposit.

It holds, per cell: sample, unit, condition, gene-expression UMIs and genes, every guide seen with its UMIs, the call
and the rule that made it, the perturbation label and the control kind. Per guide: target, kind and cells. The
expression stays in the deposit's matrices; their paths and the cells' order are recorded.

The confirmed design (pertura.design/v1, as design_service writes it) gives the target map, the controls and their
kinds, the unit and the conditions. Three keys say how the cells are labelled, with defaults:
- perturbation.assignment: {"rule": "deposit"} (the deposit's calls: one target, or "multi"); {"rule": "dominance",
  "min_umis": 3, "ratio": 3, "source": "matrix" | "calls", "one_target": "any" | "min_umis", "umis_from": "calls" |
  "matrix"} (UMIs summed per target: the top target needs min_umis and ratio times the next's; a lone target needs
  min_umis too unless one_target is "any"; with source "calls", umis_from "matrix" weighs a cell's several called
  targets by all their guides' matrix UMIs); {"rule": "combination", "min_umis": 2, "min_share": 0.1, "source": ..}
  (co-injected or co-delivered guides: the label is every target holding min_umis and min_share of the cell's guide
  UMIs, joined as "a+b"); {"rule": "labels", "column": <cell-metadata or object column>}; {"rule":
  "per_guide_threshold", "min_umis": 2, "source": ..} (in the manner of Cell Ranger's protospacer calls: each guide's
  threshold from a two-component mixture on the log UMIs of the cells holding it, never below min_umis; a cell is
  labelled with the target of the guides over their thresholds, "multi" for two or more targets); {"rule":
  "cellranger", "min_umis": 1, "source": ..} (Cell Ranger's own protospacer calling, step by step: cellranger_calls);
  with the "labels" rule a control's name in perturbation.controls is the deposit's label for its cells;
- perturbation.sets: lists of targets delivered together; a cell carrying a whole set is labelled with the set
  ("a+b"), part of one "incomplete", guides of two sets "two sets";
- data.cell_calling: {"min_umis": .., "min_genes": ..} for matrices of every droplet;
- units.unit_column: the column that holds each cell's unit, as the agent wrote it into the cell tables
  (pertura.cell_table.annotate; pertura.hashtags.write_into_cell_tables for hashtags), or a column of the processed
  object or of the deposit's cell metadata; without it each sample is one unit (docs/adr/0041, step 4a).
Labels other than a target, set or control: "none" (no guide), "multi" (several targets), "ambiguous" (no target
dominates), "incomplete", "two sets".

Without a stated rule, the main calls are the deposit's own (Cell Ranger's protospacer calls) where it has call files,
else the "cellranger" rule on the matrix's guide UMIs; never every guide UMI as a call. The calling method is an
analysis choice (docs/build_goal_adr0022.md, step 3, item 0): wherever the matrix holds the guide UMIs, the build's
summary names the other calling methods (OTHER_CALLS, pertpy's mixture model) under calls.on_request, and the agent
runs one in the notebook (pertpy_mixture_calls) when it tries another way of calling; the build does not make them.

    python -m pertura.screen_object --root <deposit> --design <confirmed design.yaml or .json> --out <folder>
                                    [--extract-to <folder>]
"""
import argparse
import hashlib
import json
import os
import time

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .cell_table import EMPTY

SCHEMA = "pertura.screen/v1"
def _value(design, key):
    node = design
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node.get("value") if isinstance(node, dict) and "value" in node else node


def load_design(path):
    with open(path) as fh:
        if path.endswith((".yaml", ".yml")):
            import yaml
            return yaml.safe_load(fh)
        return json.load(fh)


def guide_table(design, guides, reference_labels=None):
    """guide -> (target, kind) from the confirmed target map and controls; 'feature_reference' maps take the
    reference's labels. kind: 'targeting', a control kind, or 'unknown' for guides in neither. A control guide's
    target is its group in the map (or the guide itself), so that two control groups in one cell are judged like two
    targets; the cell is labelled 'control' afterwards (control_groups)."""
    controls = {}
    for c in _value(design, "perturbation.controls") or []:
        if isinstance(c, dict):
            for g in c.get("guides") or []:
                controls[str(g)] = str(c.get("kind", "control"))
    tmap = _value(design, "perturbation.target_map")
    target_of = {}
    if tmap == "feature_reference":
        for g, t in (reference_labels or {}).items():
            target_of[str(g)] = str(t)
    elif isinstance(tmap, dict):
        for t, gs in tmap.items():
            for g in gs:
                target_of[str(g)] = str(t)
    out = {}
    for g in guides:
        g = str(g)
        if g in controls:
            out[g] = (target_of.get(g, g), controls[g])
        elif g in target_of:
            out[g] = (target_of[g], "targeting")
        else:
            out[g] = (g, "unknown")
    return out


def labels_as_targets(design, labels, reference_labels=None):
    """Under the labels rule, each label that is a guide of the confirmed target map or controls read as its target,
    as the build reads a guide call: a deposit that labels its cells by guide gives the design's targets. The other
    labels stay as read."""
    labels = [str(x) for x in labels]
    table = guide_table(design, set(labels), reference_labels)
    return [table[x][0] if table[x][1] != "unknown" else x for x in labels]


def left_out(design, table):
    """The guides in no target and no control where the design gives a target map: their cells read as without a
    guide, as the design check tells the user on the form. Without a map each guide stays its own target, flagged."""
    if _value(design, "perturbation.target_map") in (None, ""):
        return set()
    return {g for g, (_, k) in table.items() if k == "unknown"}


def control_groups(table):
    """The targets that are control groups, with their kind."""
    return {t: k for t, k in table.values() if k not in ("targeting", "unknown")}


def _dominance(per_target, min_umis, ratio, lone_any=False):
    """A label from UMIs summed per target: the top target with min_umis and ratio x the next's; a lone target with
    min_umis, or with any UMIs when lone_any. Two targets tied for the top are ambiguous at any ratio, so the order
    the guides are listed in never decides."""
    s = sorted(((t, u) for t, u in per_target.items() if u > 0), key=lambda kv: -kv[1])
    if not s:
        return "none"
    if len(s) == 1:
        return s[0][0] if lone_any or s[0][1] >= min_umis else "ambiguous"
    (t1, u1), (_, u2) = s[0], s[1]
    return t1 if u1 >= min_umis and u1 >= ratio * u2 and u1 > u2 else "ambiguous"


def mixture_threshold(x, iterations=200):
    """The point above which the upper of two Gaussian components fitted to the values x (EM) is the more likely;
    None where x has too few distinct values to fit two components."""
    x = np.asarray(x, float)
    if np.unique(x).size < 3:
        return None
    mu = np.percentile(x, [25, 75]).astype(float)
    if mu[0] == mu[1]:
        mu = np.array([x.min(), x.max()], float)
    sd = np.full(2, max(x.std() / 2, 1e-3))
    w = np.array([0.5, 0.5])
    for _ in range(iterations):
        dens = w * np.exp(-0.5 * ((x[:, None] - mu) / sd) ** 2) / sd
        r = dens / np.maximum(dens.sum(1, keepdims=True), 1e-300)
        n = r.sum(0)
        w, mu = n / len(x), (r * x[:, None]).sum(0) / np.maximum(n, 1e-12)
        sd = np.sqrt(np.maximum((r * (x[:, None] - mu) ** 2).sum(0) / np.maximum(n, 1e-12), 1e-6))
    lo, hi = np.argsort(mu)
    grid = np.linspace(mu[lo], x.max(), 512)
    post_hi = w[hi] * np.exp(-0.5 * ((grid - mu[hi]) / sd[hi]) ** 2) / sd[hi]
    post_lo = w[lo] * np.exp(-0.5 * ((grid - mu[lo]) / sd[lo]) ** 2) / sd[lo]
    above = np.flatnonzero(post_hi > post_lo)
    return float(grid[above[0]]) if len(above) else None


def guide_thresholds(seen, min_umis=2):
    """Per guide, the UMIs a cell needs to be called for it, in the manner of Cell Ranger's protospacer calls: where a
    two-component mixture on log10 UMIs (of the cells with any) turns to the upper component; never below min_umis;
    min_umis where the mixture cannot be fitted."""
    per = {}
    for v in seen:
        for g, u in v:
            if u > 0:
                per.setdefault(g, []).append(u)
    out = {}
    for g, us in per.items():
        t = mixture_threshold(np.log10(np.asarray(us, float)))
        out[g] = max(min_umis, int(np.ceil(10 ** t)) if t is not None else min_umis)
    return out


def _thresholds_key(seen, groups, min_umis):
    """What the fits read: each cell's group and its guides' UMIs, the cells in order (the mixtures start from
    k-means++ draws, so another order of the cells is another input), and the floor."""
    h = hashlib.sha256(json.dumps(float(min_umis)).encode())
    for gr, v in zip(groups, seen):
        h.update(json.dumps([str(gr), sorted([str(g), float(u)] for g, u in v if u > 0)]).encode() + b"\n")
    return h.hexdigest()


def cellranger_thresholds(seen, groups=None, min_umis=1, cache=None, fit=True):
    """Per group and guide, the UMIs a cell needs to be called for the guide, as Cell Ranger calls protospacers
    (cellranger/feature/feature_assigner.py, call_presence_with_gmm_ab, the same in releases 7.2.0 to 10.0.0): a
    two-component Gaussian mixture with one shared variance (scikit-learn, covariance_type "tied", n_init 10,
    random_state 0) on log10(1 + UMIs) over every cell of the group, the cells without the guide's UMIs among them; a
    cell is called when it falls in the component with the higher mean and has at least min_umis UMIs (Cell Ranger's
    min_crispr_umi_threshold, from release 8.0.1; none before, which min_umis 1 gives). A group is one Cell Ranger
    run: a sample, or a capture of an aggregated matrix. Returns {(group, guide): UMIs}, None where no cell is
    called; in one dimension the mixture's boundary is one point, so a cell at or above it is called.
    cache: a folder where the thresholds are kept by what the fits read, so a later call on the same cells (the
    measurement's, a proposed rule's check, the build's) reads them instead of fitting again. fit False: the kept
    thresholds only, None where none are kept (a check that answers at once)."""
    groups = np.asarray(["all"] * len(seen) if groups is None else groups, dtype=object)
    path = key = None
    if cache:
        key = _thresholds_key(seen, groups, min_umis)
        path = os.path.join(cache, f"cellranger_thresholds-{key[:16]}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                kept = json.load(fh)
            if kept.get("key") == key:
                return {(gr, g): v for gr, g, v in kept["thresholds"]}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if not fit:
        return None
    from sklearn import mixture
    from threadpoolctl import threadpool_limits
    out = {}
    for gr in pd.unique(groups):
        idx = np.flatnonzero(groups == gr)
        per = {}
        for k, i in enumerate(idx):
            for g, u in seen[i]:
                if u > 0:
                    per.setdefault(g, ([], []))
                    per[g][0].append(k)
                    per[g][1].append(u)
        for g, (ks, us) in per.items():
            x = np.zeros(len(idx))
            x[ks] = us
            if x.max() == 0 or len(idx) < 2:
                out[(gr, g)] = None
                continue
            lx = np.log10(1 + x).reshape(-1, 1)
            # one thread: on one dimension the threads cost more than they give (GSE263761, 3,000 cells: 49 s on
            # twelve, 27 s on one, the same thresholds)
            with threadpool_limits(1):
                gmm = mixture.GaussianMixture(n_components=2, n_init=10, covariance_type="tied",
                                              random_state=0).fit(lx)
            called = (gmm.predict(lx) == int(np.argmax(gmm.means_))) & (x >= min_umis)
            out[(gr, g)] = float(x[called].min()) if called.any() else None
    if path:
        try:  # written whole and then moved into place, so a reader never sees half a file
            os.makedirs(cache, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"key": key, "thresholds": [[gr, g, v] for (gr, g), v in out.items()]}, fh)
            os.replace(tmp, path)
        except OSError:  # a folder that cannot be written keeps nothing; the thresholds stand
            pass
    return out


OTHER_CALLS = ("pertpy_mixture",)  # the other calling methods, run on request (calls.on_request)


def calling_calls(rule, other_calls):
    """The package calls behind the main and the other calls (question_modules.packages)."""
    from .question_modules import packages
    out = []
    if rule.get("rule") == "cellranger":
        out.append(packages.call("scikit-learn", "mixture.GaussianMixture", layer="raw_counts",
                                 formula="log10(1 + UMIs), every cell of a Cell Ranger run",
                                 contrast="the component with the higher mean", n_components=2, n_init=10,
                                 covariance_type="tied", random_state=0, min_umis=rule.get("min_umis", 1)))
    if "pertpy_mixture" in other_calls:
        out.append(packages.call("pertpy", "pp.GuideAssignment.assign_mixture_model", layer="raw_counts",
                                 formula="per guide, its defaults (poisson_gauss_mixture)",
                                 contrast="each Cell Ranger run apart"))
    return out


def pertpy_mixture_calls(weights, groups=None):
    """Each cell's called guides by pertpy's GuideAssignment.assign_mixture_model (its defaults) on the matrix's guide
    UMIs, fitted per Cell Ranger run: [[guide, ...]] per cell, None for a cell with more guides than its
    max_assignments_per_cell. Raises ImportError where pertpy or its JAX backend is not installed."""
    import anndata as ad
    import pertpy as pt
    groups = np.asarray(["all"] * len(weights) if groups is None else groups, dtype=object)
    out = [None] * len(weights)
    for gr in pd.unique(groups):
        idx = np.flatnonzero(groups == gr)
        names = sorted({g for i in idx for g, u in weights[i] if u > 0})
        if not names:
            for i in idx:
                out[i] = []
            continue
        col = {g: j for j, g in enumerate(names)}
        r, c, v = [], [], []
        for k, i in enumerate(idx):
            for g, u in weights[i]:
                if u > 0:
                    r.append(k)
                    c.append(col[g])
                    v.append(u)
        M = sp.csr_matrix((np.asarray(v, np.float32), (r, c)), shape=(len(idx), len(names)))
        import jax
        # on the CPU, within the job's own cores: a shared GPU may be full, and a fit there then fails
        with jax.default_device(jax.devices("cpu")[0]):
            res = pt.pp.GuideAssignment().assign_mixture_model(ad.AnnData(M, var=pd.DataFrame(index=names)),
                                                               only_return_results=True)
        for i, x in zip(idx, np.asarray(res).astype(str)):
            out[i] = [] if x in ("negative", "nan", "") else (None if x == "multiple" else x.split("+"))
    return out


def labels_of_calls(called, table):
    """Labels from each cell's called guides: its target, "none", or "multi" (None: more guides than counted)."""
    out = []
    for guides in called:
        if guides is None:
            out.append("multi")
            continue
        targets = {table.get(g, (g, "unknown"))[0] for g in guides}
        out.append("none" if not targets else (targets.pop() if len(targets) == 1 else "multi"))
    return out


def label_cells(seen, table, rule, sets=None, weights=None, groups=None, cache=None, fitted=None):
    """seen: per cell [(guide, umis)] (the guides the rule's source saw); weights: per cell [(guide, umis)] of every
    guide in the matrix, used for cells with several targets when the rule says umis_from 'matrix'; table: guide ->
    (target, kind); groups: per cell its Cell Ranger run (the "cellranger" rule fits each apart); cache: the folder
    the "cellranger" rule's thresholds are kept in (cellranger_thresholds); fitted: those thresholds where they are
    known already. Returns (labels, calls) per cell."""
    member = {}
    for st in sets or []:
        name = "+".join(sorted(st))
        for t in st:
            member[t] = name
    lone_any = rule.get("one_target") == "any"
    thresholds = guide_thresholds(seen, rule.get("min_umis", 2)) if rule.get("rule") == "per_guide_threshold" else {}
    # the "cellranger" rule's floor after the fit: in one dimension the mixture's boundary is one point, so the floor's
    # threshold is the larger of the fit's and the floor, and one fit (the measurement's, kept) serves every floor
    floor = rule.get("min_umis", 1)
    cr = ((fitted if fitted is not None else cellranger_thresholds(seen, groups, 1, cache=cache))
          if rule.get("rule") == "cellranger" else {})
    gr = np.asarray(["all"] * len(seen) if groups is None else groups, dtype=object)
    labels, calls = [], []
    for i, v in enumerate(seen):
        per = {}
        for g, u in v:
            t = table.get(g, (g, "unknown"))[0]
            per[t] = per.get(t, 0) + u
        if rule.get("rule") == "per_guide_threshold":
            over = {table.get(g, (g, "unknown"))[0] for g, u in v if u >= thresholds.get(g, np.inf)}
            lab = "none" if not over else (over.pop() if len(over) == 1 else "multi")
        elif rule.get("rule") == "cellranger":
            over = {table.get(g, (g, "unknown"))[0] for g, u in v
                    if cr.get((gr[i], g)) is not None and u >= max(cr[(gr[i], g)], floor)}
            lab = "none" if not over else (over.pop() if len(over) == 1 else "multi")
        elif rule.get("rule") == "dominance":
            if len(per) > 1 and weights is not None and rule.get("umis_from") == "matrix":
                w = {}
                for g, u in weights[i]:
                    t = table.get(g, (g, "unknown"))[0]
                    w[t] = w.get(t, 0) + u
                lab = _dominance(w, rule.get("min_umis", 3), rule.get("ratio", 3), lone_any)
            else:
                lab = _dominance(per, rule.get("min_umis", 3), rule.get("ratio", 3), lone_any)
        elif rule.get("rule") == "combination":  # every target with min_umis and min_share of the cell's guide UMIs
            tot = sum(per.values())
            keep = sorted(t for t, u in per.items() if u >= rule.get("min_umis", 2)
                          and u >= rule.get("min_share", 0.1) * tot)
            lab = "+".join(keep) if keep else "none"
        else:  # the deposit's calls as they are
            lab = "none" if not per else (next(iter(per)) if len(per) == 1 else "multi")
        if member and per:
            in_sets = {member[t] for t in per if t in member}
            if len(in_sets) >= 2:
                lab = "two sets"
            elif len(in_sets) == 1:
                name = in_sets.pop()
                lab = name if set(name.split("+")) <= set(per) else "incomplete"
        labels.append(lab)
        calls.append(";".join(f"{g}:{u}" for g, u in v))
    return labels, calls


def build(root, design, out_dir, extract_to=None, parsed=None, cell_tables=None):
    """Build the screen object of a deposit under its confirmed design and write it to out_dir. Returns the summary
    (screen.json). parsed: the folder of the parsed text matrices (the measurement's), read instead of parsing them
    again, and filled in parallel where a matrix is not there yet. cell_tables: the project's cell-table folder, read
    instead of the deposit's files once the deposit has been read into it (pertura.cell_table)."""
    from .design_audit import detectors as dt
    from .design_audit.audit import guide_readouts
    from . import limits
    from .design_audit.readers import (GUIDE, declared_types, open_deposit, parse_matrices, read_calls,
                                       read_feature_reference, read_matrix)
    t0 = time.time()
    dep, extracted, _ = open_deposit(root, extract_to, cell_tables=cell_tables)
    ref = read_feature_reference(dep["feature_references"][0]) if dep["feature_references"] else None
    rule = _value(design, "perturbation.assignment") or {"rule": "auto"}
    expression = set()  # the expression features' names, for the reading checks
    other_calls, not_available, on_request = {}, {}, {}
    sets = _value(design, "perturbation.sets") or []
    calling = _value(design, "data.cell_calling") or {}
    unit_column = _unit_column(design)
    from .design_gate import condition_factors
    factors = condition_factors(_value(design, "experiment.conditions"))
    if any(f["by"] is None and len(f["levels"]) > 1 for f in factors):  # never a quiet condition-less build
        raise ValueError("experiment.conditions names conditions that no column holds (by sample or by unit, which "
                         "the build no longer reads): write each cell's condition into the cell tables "
                         "(pertura.cell_table.annotate; one value per sample gives all its cells that value) and give "
                         "{column, names}, or [{name, levels, column}] for crossed factors")
    factor_columns = [f["map"] for f in factors if f["by"] == "column"]  # factors read from a cell column
    missing_columns = set()  # unit or condition columns that neither the cell tables nor the cell metadata hold
    dropped = set()  # guides in no target and no control of the design's map (left_out)
    if rule.get("rule") == "auto" and not dep["samples"]:  # a processed object: its own calls
        rule = {"rule": "deposit"}
    if not dep["samples"] and dep["objects"]:
        cells, dropped = _from_object(dep, design, rule, ref, unit_column)
    else:
        declared = declared_types(ref)
        readouts = guide_readouts(dep["samples"])
        samples = [s for s in dep["samples"] if s not in readouts]
        if rule.get("rule") == "auto":  # the deposit's own calls where it has them, else Cell Ranger's own method
            rule = {"rule": "deposit"} if any(s.get("calls") for s in samples) else {"rule": "cellranger",
                                                                                      "min_umis": 1}
        parts = []
        if parsed:
            parse_matrices(samples, declared, parsed, max(1, limits.THREAD_LIMIT - 1))
        for s in samples:
            a = read_matrix(s, declared, parsed)
            ft = a.var["feature_types"].astype(str).to_numpy()
            names = a.var["name"].astype(str).to_numpy()
            X = sp.csr_matrix(a.X)
            gex = np.flatnonzero(ft == "Gene Expression")
            expression.update(map(str, names[gex]))
            umis = np.asarray(X[:, gex].sum(1)).ravel()
            ngenes = np.asarray((X[:, gex] > 0).sum(1)).ravel()
            keep = np.ones(a.n_obs, bool)
            if calling:
                keep = (umis >= calling.get("min_umis", 0)) & (ngenes >= calling.get("min_genes", 0))
            gi = np.flatnonzero(ft == GUIDE)
            G = X[:, gi].tocsr()
            gnames = names[gi]
            bcs = np.asarray(a.obs_names).astype(str)
            source = rule.get("source") or ("calls" if s.get("calls") else "matrix")
            calls = {}
            if source == "calls" and s.get("calls"):
                guide_ids = a.var.loc[ft == GUIDE, "gene_ids"].astype(str).tolist() if "gene_ids" in a.var else None
                calls = dt.parse_calls(read_calls(s["calls"]), list(gnames), guide_ids)
            seen, weights = [], []
            for i in np.flatnonzero(keep):
                row = G[i]
                in_matrix = [(str(gnames[j]), int(v)) for j, v in zip(row.indices, row.data) if v > 0]
                seen.append(calls.get(bcs[i], []) if source == "calls" else in_matrix)
                weights.append(in_matrix)
            unit = np.full(int(keep.sum()), s["name"], dtype=object)
            unit_call = np.full(int(keep.sum()), "", dtype=object)  # 'one', or 'none' where the column holds no value
            if unit_column:  # each cell's unit from the column the design names
                vals = _cell_column(a, dep, unit_column, bcs)
                if vals is None:
                    raise ValueError(f"units.unit_column {unit_column!r}: neither the cell tables nor the deposit's "
                                     f"cell metadata give it for the cells of {s['name']}, so the units cannot be told "
                                     "apart as confirmed; write it into the cell tables (pertura.cell_table.annotate) "
                                     "or correct the design")
                if keep.any() and np.isin(vals[keep], EMPTY).all():
                    raise ValueError(f"units.unit_column {unit_column!r} holds no value for any cell of {s['name']}: "
                                     "the cell metadata's barcodes do not match the matrix's")
                unit = np.array([v if v not in EMPTY else f"{s['name']}:unassigned" for v in vals[keep]],
                                dtype=object)
                unit_call = np.where(np.char.endswith(unit.astype(str), ":unassigned"), "none", "one")
            part = {"barcode": [f"{s['name']}:{b}" for b in bcs[keep]], "sample": s["name"], "unit": unit,
                    "umis": umis[keep], "genes": ngenes[keep], "seen": seen, "weights": weights,
                    "unit_call": unit_call}
            if rule.get("rule") == "labels":  # the deposit's own labels, from the cell table or its cell metadata
                vals = _cell_column(a, dep, rule.get("column"), bcs)
                if vals is not None:
                    part["label_value"] = ["none" if v in ("", "nan", "None") else v for v in vals[keep]]
            for col in factor_columns:
                vals = _cell_column(a, dep, col, bcs)
                if vals is None:
                    missing_columns.add(str(col))
                part[f"_factor_{col}"] = ["" if vals is None or v in ("nan", "None") else v
                                          for v in (vals[keep] if vals is not None else [""] * int(keep.sum()))]
            parts.append(pd.DataFrame(part))
            del a, X, G
        cells = pd.concat(parts, ignore_index=True)
        guides = sorted({g for v in cells["seen"] for g, _ in v} | {g for v in cells["weights"] for g, _ in v})
        table = guide_table(design, guides, dt.reference_labels(ref))
        dropped = left_out(design, table)
        if dropped:  # before any call, so a cell holding only these reads as without a guide
            for col in ("seen", "weights"):
                cells[col] = [[(g, u) for g, u in v if g not in dropped] for v in cells[col]]
        # shortcut: a rule fitted per Cell Ranger run is fitted per cell table; an aggregated matrix of several runs
        # is fitted as one, so split it into one table per run where such a rule is used
        cells["label"], cells["guides"] = label_cells(list(cells["seen"]), table, rule, sets,
                                                      list(cells["weights"]), groups=list(cells["sample"]),
                                                      cache=parsed)
        if any(cells["weights"]):  # the matrix holds the guide UMIs: the other calls can be made, on request
            on_request["pertpy_mixture"] = ("pertpy's GuideAssignment.assign_mixture_model on each cell's guide UMIs, "
                                            "fitted per Cell Ranger run (pertpy_mixture_calls), run in the notebook "
                                            "when another way of calling is tried, on the guide UMIs read again from "
                                            "the deposit's matrices: the screen object does not keep them, and the "
                                            "measurement's arrays.npz holds them without the cells' barcodes")
        if rule.get("rule") == "labels":
            if "label_value" in cells:
                cells = cells.assign(label=cells.pop("label_value"))
            else:
                cells = _labels_from_metadata(dep, cells, rule["column"])
            cells["label"] = labels_as_targets(design, cells["label"], dt.reference_labels(ref))
        cells = cells.drop(columns=["seen", "weights"])
    guides_all = sorted({g.split(":")[0] for v in cells["guides"] for g in v.split(";") if g})
    table = guide_table(design, guides_all, dt.reference_labels(ref))
    from .design_gate import delivery_of
    deliver = {(u, s): delivery_of(design, unit=u, sample=s) for u, s in set(zip(cells["unit"], cells["sample"]))}
    named = _value(design, "perturbation.delivery")
    by = (named.get("by_unit") or named.get("by_sample") or {}) if isinstance(named, dict) else {}
    if by and not any(v is not None for v in deliver.values()):  # said, never a quiet loss of the pairs
        raise ValueError(f"perturbation.delivery names {', '.join(sorted(map(str, by))[:8])}, but the cells' units "
                         f"and samples are {', '.join(sorted(set(map(str, cells['unit'])))[:8])}; the delivery "
                         "cannot be applied as confirmed. Correct the units or the delivery")
    if "pairs" in deliver.values():  # where the design says pairs are delivered, each cell's label is its pair
        cells["label"] = pair_labels(cells, table, deliver)
        cells["partner"] = partner_seen(cells, table, deliver)
    kinds = control_groups(table)  # control groups are judged like targets, then labelled 'control'
    controls = [c for c in _value(design, "perturbation.controls") or [] if isinstance(c, dict)]
    for c in controls:  # a control's name, as a labels rule's cells carry it ('Non-Targeting', 'CTRL1')
        if c.get("name"):
            kinds.setdefault(str(c["name"]), str(c.get("kind", "control")))
    cells["control_group"] = [x if x in kinds else "" for x in cells["label"]]
    cells["label"] = ["control" if x in kinds else x for x in cells["label"]]
    for name, labels in other_calls.items():
        cells[f"label_{name}"] = ["control" if x in kinds else x for x in labels]
    cells["control_kind"] = [kinds.get(gr, "") if lab == "control" else ""
                             for lab, gr in zip(cells["label"], cells["control_group"])]
    if controls and not (cells["label"] == "control").any():  # said, never a quiet loss of the controls
        named = "; ".join(str(c.get("name") or ", ".join(map(str, (c.get("guides") or [])[:3]))) for c in controls)
        seen = ", ".join(f"{k} ({v:,})" for k, v in cells["label"].value_counts().head(8).items())
        raise ValueError(f"the confirmed controls ({named}) match no cell; the cells' labels are {seen}. Under the "
                         "labels rule a control's name is the label its cells carry; under the guides' rules its "
                         "guides are the library's. Correct the controls or the assignment, and build again")
    from . import reading_checks  # what was read, against facts any correctly read screen satisfies
    unnamed = []
    # a deposit's own word for cells of no target ("zMulti", "Negative") holds no target, where its labels and the
    # design's targets share a name: labels spelt otherwise throughout stay as read, and both checks name them
    if rule.get("rule") == "labels" and set(cells["label"]) & reading_checks._targets(design, table):
        unnamed = reading_checks.outside_labels(cells["label"], design, table)
        cells["label"] = ["ambiguous" if x in unnamed else x for x in cells["label"]]
    levels = []  # each factor's level per cell
    for f in factors:
        if f["by"] == "column":
            key = f"_factor_{f['map']}"
            if key not in cells and not dep["samples"] and dep["objects"]:  # a processed object's own column
                import anndata as ad
                obs = ad.read_h5ad(dep["objects"][0], backed="r").obs
                if f["map"] in obs:
                    from .question_modules.data import _text
                    cells[key] = pd.Series(_text(obs, f["map"]), index=obs.index).reindex(cells["barcode"]).fillna(
                        "").values
                else:
                    missing_columns.add(str(f["map"]))
            levels.append(list(cells.pop(key)) if key in cells else [""] * len(cells))
        else:  # one condition named, read by no cell
            levels.append([""] * len(cells))
    partly = condition_reach(factors, levels, cells, missing_columns)
    if len(factors) > 1:  # crossed factors: each kept, and their combination is the cell's condition
        for f, v in zip(factors, levels):
            cells[f"condition_{f['name']}"] = v
        from .design_gate import join_levels
        cells["condition"] = [join_levels(x) if all(x) else "" for x in zip(*levels)]
    else:
        cells["condition"] = levels[0] if levels else ""
    os.makedirs(out_dir, exist_ok=True)
    cells.to_csv(os.path.join(out_dir, "cells.tsv.gz"), sep="\t", index=False)
    per_label = cells["label"].value_counts()
    own = [gr if lab == "control" else lab for lab, gr in zip(cells["label"], cells["control_group"])]
    gt = pd.DataFrame([{"guide": g, "target": t, "kind": k,
                        "cells": int(sum(g in _guides_of(v) for v, lab in zip(cells["guides"], own) if lab == t))}
                       for g, (t, k) in table.items()])
    from .design_gate import GUIDE_ATTRIBUTES, guide_attributes
    attrs = guide_attributes(design)  # each guide's mode, gene, element and vector, where the design gives them
    for a in GUIDE_ATTRIBUTES:
        if any(a in v for v in attrs.values()):
            gt[a] = [attrs.get(g, {}).get(a, "") for g in gt["guide"]]
    gt.to_csv(os.path.join(out_dir, "guides.tsv"), sep="\t", index=False)
    by_unit = cells.groupby("unit")["label"]
    shares = by_unit.value_counts(normalize=True).unstack(fill_value=0.0)
    units = {str(u): {"cells": int(n), **{k: round(float(shares.at[u, k]), 3) for k in ("none", "multi", "ambiguous")
                                          if k in shares.columns},
                      "control_cells": int((cells["label"][cells["unit"] == u] == "control").sum())}
             for u, n in by_unit.size().items()}
    flags = []
    alone = sorted(g for g, (_, k) in table.items() if k == "unknown")
    if alone:  # said, never quiet: the corpus check's GSE327731 had no target map, and each guide became a target
        flags.append({"flag": "guides in no target", "guides": len(alone), "of": len(table), "such as": alone[:5],
                      "note": "the design's target map and controls hold none of these guides, so each is read as "
                              "its own target; give each target's guides in perturbation.target_map and build "
                              "again"})
    if dropped:  # the design check's flag said so on the form; the build does what it said
        flags.append({"flag": "guides left out", "guides": len(dropped), "such as": sorted(dropped)[:5],
                      "note": "in no target and no control of the design's target map, so their cells read as "
                              "without a guide; give them a target or a control to count them"})
    if missing_columns:  # said, never hidden: the units or conditions fell back to the samples or to none
        flags.append({"flag": "columns not found", "columns": sorted(missing_columns),
                      "note": "neither the cell tables nor the deposit's cell metadata hold them; units are the "
                              "samples and conditions empty where they were to come from these columns"})
    big = {u: v for u, v in units.items() if v["cells"] >= 100}
    several = {u: v.get("multi", 0) + v.get("ambiguous", 0) for u, v in big.items()}
    combinatorial = bool(sets) or (cells["label"].astype(str).str.contains(r"\|").mean() >= 0.1)
    # reported as numbers, without a flag at a ratio: no published screen names one
    plain = {u: x for u, x in several.items() if x < 0.5 or not combinatorial}  # pairs by design: compared apart
    ambient = ({"lowest": round(min(plain.values()), 3), "highest": round(max(plain.values()), 3)}
               if len(plain) > 1 else None)
    ctrl_cells = gt.loc[gt["kind"].isin([k for k in gt["kind"].unique() if k not in ("targeting", "unknown")]),
                        "cells"]
    ctrl_cells = ctrl_cells[ctrl_cells > 0]
    control_guide_cells = ({"min": int(ctrl_cells.min()), "median": float(ctrl_cells.median()),
                            "max": int(ctrl_cells.max())} if len(ctrl_cells) else None)
    if partly:
        flags.append({"flag": "conditions reaching some cells", "conditions": partly,
                      "note": "cells whose sample or unit the condition does not name have no condition"})
    if (cells["label"] == "control").any():
        lacking = sorted(u for u, v in big.items() if v["control_cells"] < 10)
        if lacking:
            flags.append({"flag": "units without control cells", "units": lacking[:20],
                          "note": "these units need another backdrop, said so"})
    summary = {"schema": SCHEMA, "deposit": os.path.abspath(root), "extracted": extracted, "flags": flags,
               "parsed": os.path.abspath(parsed) if parsed else None,
               "cell_tables": os.path.abspath(cell_tables) if cell_tables else None,
               "by_unit": units, "several_or_ambiguous_share_by_unit": ambient,
               "control_guide_cells": control_guide_cells,
               "rule": rule, "sets": sets, "cell_calling": calling, "unit_column": unit_column,
               "calls": {"main": rule.get("rule"), "others": sorted(other_calls), "on_request": on_request,
                         "not_available": not_available, "package_calls": calling_calls(rule, other_calls)},
               "cells": int(len(cells)), "labels": {str(k): int(v) for k, v in per_label.items()},
               "units": {str(k): int(v) for k, v in cells["unit"].value_counts().items()},
               "cells_sha256": hashlib.sha256("\n".join(cells["barcode"]).encode()).hexdigest(),
               "design_sha256": hashlib.sha256(json.dumps(design, sort_keys=True, default=str).encode()).hexdigest(),
               "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.time() - t0, 1)}
    # what was read, against facts any correctly read screen satisfies
    summary["reading_checks"] = reading_checks.check(cells, design, table, genes=expression) + (
        [{"check": "labels outside the design", "labels": unnamed, "read_as": "ambiguous",
          "text": f"Cell labels that are no target or control of the design, read as cells of no target: "
                  f"{len(unnamed)} ({reading_checks._some(unnamed)})"}] if unnamed else [])
    summary["samples"] = reading_checks.samples_read(cells)
    summary["build_id"] = build_id(summary)
    # the counts cache written by the build, before the summary: a reader that finds screen.json finds the counts
    # too and only reads them (a notebook may not write here, and two readers would assemble them twice)
    read = json.loads(json.dumps(summary, default=str))
    counts = load(out_dir, meta=read)
    summary["counts_from"] = read.get("counts_from")  # said: where the counts the modules read came from
    if read.get("antibody_features"):  # said: the antibodies whose raw counts sit beside the genes (antibody_counts)
        summary["antibody_features"] = read["antibody_features"]
    if summary["counts_from"] and not summary["counts_from"].endswith(("count matrices", "(X), which holds counts")):
        summary["flags"].append({"flag": "the counts' source", "counts_from": summary["counts_from"]})
    # the cells overview, from which the quality rule is proposed and confirmed after the build (pertura.cells_overview)
    from . import cells_overview
    cells_overview.write(out_dir, counts, dep.get("objects") or ())
    summary["cells_overview"] = cells_overview.FOLDER
    del counts
    with open(os.path.join(out_dir, "screen.json.tmp"), "w") as fh:  # written whole: a reader never sees half
        json.dump(summary, fh, indent=1, default=str)
    os.replace(os.path.join(out_dir, "screen.json.tmp"), os.path.join(out_dir, "screen.json"))
    return summary


def build_id(summary):
    """A build's ID: from the design it read, the cells it made and when it was built (a summary from before the IDs
    gets the same ID from the same fields)."""
    if summary.get("build_id"):
        return summary["build_id"]
    raw = "|".join(str(summary.get(k) or "") for k in ("design_sha256", "cells_sha256", "built"))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def current_build(screen_dir):
    """The ID of the screen object's build in this folder, or None before a build."""
    path = os.path.join(screen_dir, "screen.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return build_id(json.load(fh))


def cells_changed(old_cells, new_cells, columns=("label", "unit", "condition")):
    """What a rebuild changed in the cells: {changed: whether any cell's label, unit or condition differs or a cell
    came or went, targets: the labels whose cells changed (every target when the control cells did), controls: whether
    the control cells changed, cells_gone, cells_new}."""
    from .question_modules.data import NOT_TARGETS
    keep = ["barcode"] + [c for c in columns if c in old_cells and c in new_cells]
    a = old_cells[keep].astype(str).set_index("barcode")
    b = new_cells[keep].astype(str).set_index("barcode")
    gone, new = a.index.difference(b.index), b.index.difference(a.index)
    both = a.index.intersection(b.index)
    diff = (a.loc[both] != b.loc[both]).any(axis=1)
    moved = both[diff.to_numpy()]
    labels = set(a.loc[moved, "label"]) | set(b.loc[moved, "label"]) | set(a.loc[gone, "label"]) | set(
        b.loc[new, "label"]) if "label" in keep else set()
    controls = "control" in labels
    targets = sorted(set(b["label"]) - set(NOT_TARGETS)) if controls else sorted(labels - set(NOT_TARGETS))
    return {"changed": bool(len(gone) or len(new) or len(moved)), "targets": targets, "controls": controls,
            "cells_gone": int(len(gone)), "cells_new": int(len(new)), "cells_changed": int(len(moved))}


def _guides_of(text):
    """The guides of a cell's called guides ('g1:12;g2:3'): each up to its last ':', so a guide's own ':' stays."""
    return [x.rsplit(":", 1)[0] for x in str(text).split(";") if x]


def pair_labels(cells, table, deliver):
    """Where the design says a sample or unit delivers pairs (ADR 0026, D2), each cell's label from every guide
    called in it: its two targets as 'a|b'; its one target when its other guide is a control; 'multi' for three or
    more; a control group when it carries control guides alone. deliver: {(unit, sample): 'pairs' | 'singles' |
    None}; elsewhere the labels stay."""
    out = []
    for lab, text, u, s in zip(cells["label"], cells["guides"], cells["unit"], cells["sample"]):
        if deliver.get((u, s)) != "pairs":
            out.append(lab)
            continue
        guides = [g for g in _guides_of(text) if g in table]
        targets = sorted({table[g][0] for g in guides if table[g][1] == "targeting"})
        if len(targets) > 2:
            out.append("multi")
        elif targets:
            out.append("|".join(targets))
        elif guides and all(table[g][1] not in ("targeting", "unknown") for g in guides):
            out.append(table[guides[0]][0])  # a control group, labelled 'control' with the others
        else:
            out.append(lab)
    return out


def partner_seen(cells, table, deliver):
    """Per cell of a capture of pairs whose label is one target: "seen" when a second guide is called beside the
    target's (its construct's control guide, or a second guide of the target), "not seen" when the target's guide
    is called alone; '' elsewhere. A single without its partner may be a pair whose other guide went uncalled."""
    out = []
    for lab, text, u, s in zip(cells["label"], cells["guides"], cells["unit"], cells["sample"]):
        guides = [g for g in _guides_of(text) if g in table]
        targets = {table[g][0] for g in guides if table[g][1] == "targeting"}
        if deliver.get((u, s)) != "pairs" or len(targets) != 1 or "|" in str(lab):
            out.append("")
        else:
            out.append("seen" if len(set(guides)) >= 2 else "not seen")
    return out


def condition_reach(factors, levels, cells, missing_columns=()):
    """The confirmed conditions read from columns must reach the cells: one that reaches no cell stops the build,
    naming its column; one that reaches only some cells is returned ({name: cells reached, of all}) for a flag. A
    column the build could not find is flagged already (columns not found)."""
    partly = {}
    for f, v in zip(factors, levels):
        if f["by"] != "column":
            continue
        reached = sum(1 for x in v if x)
        if reached == len(v):
            continue
        if reached == 0 and str(f["map"]) in missing_columns:
            continue  # flagged already: columns not found
        if reached == 0:
            raise ValueError(f"the confirmed condition {f['name']!r} reaches no cell: its column '{f['map']}' holds "
                             f"no value for any of the {len(v):,} cells; write it (pertura.cell_table.annotate) or "
                             "correct the design")
        partly[f["name"]] = f"{reached} of {len(v)}"
    return partly


def _labels_from_metadata(dep, cells, column):
    """The deposit's own labels: a cell-metadata table keyed by the matrix's barcodes, column `column`. A cell's
    barcode is its sample's name, ':' and the matrix's barcode; a sample's name may hold ':' itself, so the sample
    is taken off by its name where the cells carry it, else at the last ':'."""
    from .design_audit import inventory as inv
    if "sample" in cells:
        raw = pd.Series([b[len(s) + 1:] if b.startswith(f"{s}:") else b.rsplit(":", 1)[-1]
                         for b, s in zip(cells["barcode"].astype(str), cells["sample"].astype(str))],
                        index=cells.index)
    else:
        raw = cells["barcode"].astype(str).str.rsplit(":", n=1).str[-1]
    for tpath in dep["tables"]:
        try:
            table = pd.read_csv(tpath, sep=None, engine="python")
        except Exception:
            continue
        _, _, t, _, _ = inv.cell_metadata(table, raw.to_numpy(), [])
        if t is not None and column in t.columns:
            lab = t[column].reindex(raw.to_numpy()).fillna("none").astype(str).to_numpy()
            return cells.assign(label=lab)
    raise ValueError(f"no cell-metadata table with a column '{column}'")


def _unit_column(design):
    """The column that holds each cell's unit (units.unit_column), or None where each sample is one unit. A design
    that names how units are told apart the retired ways (docs/adr/0041, step 4a) stops here, never read as one unit
    per sample."""
    from .design_audit.checks import RETIRED_UNIT_KEYS
    retired = [f"units.{k}" for k in RETIRED_UNIT_KEYS if _value(design, f"units.{k}") is not None]
    if retired or isinstance(_value(design, "units.independent_unit"), dict):
        raise ValueError(f"the design gives {', '.join(retired) or 'units.independent_unit as a rule'}, which the "
                         "build no longer reads: write each cell's unit into the cell tables "
                         "(pertura.cell_table.annotate; pertura.hashtags.write_into_cell_tables for hashtags) and "
                         "name the column in units.unit_column")
    return _value(design, "units.unit_column") or None


def _cell_column(a, dep, column, barcodes):
    """Each cell's value in `column`: the cell table's own column, else a cell-metadata table of the deposit keyed
    by the matrix's barcodes; None when neither holds it."""
    from .question_modules.data import _text  # a missing value as '' (pandas 3's astype(str) keeps NaN)
    if column and column in a.obs.columns:
        return _text(a.obs, column)
    from .design_audit import inventory as inv
    for tpath in dep.get("tables") or []:
        try:
            table = pd.read_csv(tpath, sep=None, engine="python")
        except Exception:
            continue
        _, _, t, _, _ = inv.cell_metadata(table, np.asarray(barcodes), [])
        if t is not None and column in t.columns:
            return _text(t.reindex(barcodes), column)
    return None


def counts_source(a, n=2000):
    """Where a processed object holds its raw counts, read from its first n cells as the measurement reads a matrix
    (design_audit.inventory.matrix_content): the first of its matrix (X), its raw matrix (.raw) and its layers that holds
    whole numbers; else X as log(1 + counts) where exp(X) - 1 gives whole numbers; else exp(X) - 1 of X, which are no
    counts. Returns (where: "X", "raw" or a layer's name; read as exp(X) - 1 or not; the words the summary gives)."""
    from .design_audit.inventory import INTEGER_TOL

    def block(m):
        b = m[:n]
        return b.toarray() if sp.issparse(b) else np.asarray(b)

    def whole(v, tol=INTEGER_TOL):
        nz = np.asarray(v, dtype=np.float64)
        nz = nz[nz != 0]
        return not len(nz) or (np.mean(np.abs(nz - np.round(nz)) < tol) >= 0.99 and nz.min() >= 0)

    if whole(block(a.X)):
        return "X", False, "the object's matrix (X), which holds counts"
    if a.raw is not None and whole(block(a.raw.X)):
        return "raw", False, "the object's raw matrix (.raw), which holds counts"
    for name in list(a.layers.keys()):
        if whole(block(a.layers[name])):
            return name, False, f"the object's layer {name!r}, which holds counts"
    if whole(np.expm1(block(a.X)), tol=1e-2):
        return "X", True, "the object's matrix (X), which holds log(1 + counts): read as exp(X) - 1"
    return "X", True, ("the object's matrix (X), which holds neither counts nor log(1 + counts), and neither does a "
                       "raw matrix or a layer: read as exp(X) - 1, rounded, so these are not counts")


def _source_matrix(a, where):
    return a.X if where == "X" else a.raw.X if where == "raw" else a.layers[where]


def _from_object(dep, design, rule, ref, unit_column):
    """A processed object: the guide column the design names, one guide per cell; units from an object column.
    Returns the cells and the guides left out (left_out)."""
    import anndata as ad
    a = ad.read_h5ad(dep["objects"][0], backed="r")
    obs = a.obs
    col = rule.get("column")
    if not col or col not in obs:
        raise ValueError("a processed object needs perturbation.assignment {'rule': 'labels', 'column': <guide "
                         "column>}")
    from .question_modules.data import UNASSIGNED, _text  # a missing value as '' (pandas 3's astype(str) keeps NaN)
    g = _text(obs, col)
    unit_col = unit_column
    umis = np.zeros(a.n_obs)
    where, logged, _ = counts_source(a)  # counts per cell, from where the object holds them (as the build reads them)
    m = _source_matrix(a, where)
    for r in range(0, a.n_obs, 2000):
        x = m[r:r + 2000]
        x = x.toarray() if sp.issparse(x) else np.asarray(x)
        umis[r:r + 2000] = (np.round(np.expm1(x)) if logged else x).sum(1)
    from .design_audit import detectors as dt
    table = guide_table(design, sorted(set(g)), dt.reference_labels(ref))
    dropped = left_out(design, table)
    cells = pd.DataFrame({"barcode": obs.index.astype(str), "sample": os.path.basename(dep["objects"][0]),
                          "unit": [u if u not in EMPTY else f"object{UNASSIGNED}" for u in _text(obs, unit_col)]
                          if unit_col in obs else "object",
                          "umis": umis, "genes": 0,
                          "guides": ["" if x in dropped or x in EMPTY else f"{x}:1" for x in g]})
    cells["label"] = [table[x][0] if x in table and table[x][1] != "unknown"
                      else "none" if x in dropped or x in ("none", "", "nan") else x for x in g]
    return cells, dropped


def load(screen_dir, cache=True, meta=None):
    """The screen object's cells with their gene-expression counts: an AnnData (cells x genes, raw counts) whose obs
    are cells.tsv.gz (sample, unit, label, condition, control_kind, control_group, umis, genes, guides). Guide
    features, and the features the design reads as guides, are left out; the antibody features' raw counts (CITE-seq,
    hashtags among them) are kept in obsm["antibody_counts"], cells x antibodies. The counts are saved beside the
    object the first time (cache), named for the cells, the deposit and the features left out, and read from there
    afterwards; the cells' columns are always read from cells.tsv.gz. meta: the object's summary, where screen.json is
    not yet written (the build, caching the counts before it)."""
    import anndata as ad
    if meta is None:
        with open(os.path.join(screen_dir, "screen.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t",  # empty fields stay empty text
                        keep_default_na=False).set_index("barcode")
    key = hashlib.sha256(json.dumps([meta.get("cells_sha256"), meta.get("deposit"),
                                     sorted(meta.get("guide_features") or []), "antibody_counts"]).encode()
                         ).hexdigest()[:12]
    path = os.path.join(screen_dir, f"counts-{key}.h5ad")
    if cache and os.path.exists(path):
        out = ad.read_h5ad(path)
        # a rebuild that finds the counts here still says where they came from and which antibodies they hold
        meta["counts_from"] = meta.get("counts_from") or out.uns.pop("counts_from", None)
        if "antibody_counts" in out.obsm:
            meta["antibody_features"] = [str(c) for c in out.obsm["antibody_counts"].columns]
        out.uns = {}
        out.obs = cells.reindex(out.obs_names)
        return out
    from .design_audit.readers import declared_types, discover, read_feature_reference, read_matrix
    dep = discover(meta["deposit"], also=meta.get("extracted") or [], cell_tables=meta.get("cell_tables"))
    antibodies = None
    if not dep["samples"] and dep["objects"]:
        a = ad.read_h5ad(dep["objects"][0])
        where, logged, came = counts_source(a)
        X = sp.csr_matrix(_source_matrix(a, where))
        if logged:
            X.data = np.round(np.expm1(X.data))
        genes = a.raw.var_names if where == "raw" else a.var_names
        a = ad.AnnData(X, obs=pd.DataFrame(index=a.obs_names.astype(str)), var=pd.DataFrame(index=genes))
        out = a[a.obs_names.isin(cells.index)].copy()
        meta["counts_from"] = came
    else:
        ref = read_feature_reference(dep["feature_references"][0]) if dep["feature_references"] else None
        declared = declared_types(ref)
        # screen objects built before 2026-11-06 name the features their design read as guides (a retired design key,
        # pertura_research.retired.design_keys_for_guide_rows_and_samples_left_out); later ones type them in the tables
        not_genes = set(meta.get("guide_features") or [])
        parts, ab_parts = [], []
        for smp in dep["samples"]:
            want = cells.index[cells.index.str.startswith(f"{smp['name']}:")]
            if not len(want):
                continue
            a = read_matrix(smp, declared, meta.get("parsed"))
            ft = a.var["feature_types"].astype(str).to_numpy()
            names = a.var["name"].astype(str).to_numpy()
            gex = (ft == "Gene Expression") & ~np.isin(names, list(not_genes))
            barcodes = pd.Index([f"{smp['name']}:{b}" for b in a.obs_names])
            kept = barcodes.isin(want)  # pandas hashes; numpy's isin compares text one value at a time
            ab = ft == "Antibody Capture"
            if ab.any():  # the columns first: picking rows and columns at once is slow on a large sparse matrix
                m = sp.csr_matrix(a.X)[:, np.flatnonzero(ab)][kept]
                ab_parts.append(pd.DataFrame(m.toarray() if sp.issparse(m) else np.asarray(m), index=barcodes[kept],
                                             columns=ad.utils.make_index_unique(pd.Index(names[ab]))))
            a = a[:, gex].copy()
            a.var_names = names[gex]
            a.var_names_make_unique()
            a.obs_names = barcodes
            parts.append(a[kept].copy())
        out = ad.concat(parts, join="inner") if len(parts) > 1 else parts[0]
        meta["counts_from"] = "the deposit's count matrices"
        if ab_parts:
            antibodies = pd.concat(ab_parts, join="inner")
            meta["antibody_features"] = [str(c) for c in antibodies.columns]
    out.X = sp.csr_matrix(out.X)
    out.obs = pd.DataFrame(index=out.obs_names.astype(str))
    out.var = pd.DataFrame(index=out.var_names.astype(str))
    out.uns = {}  # the readers' notes on the matrices (type conflicts and the like) are in the audit, not here
    if antibodies is not None:
        out.obsm["antibody_counts"] = antibodies.reindex(out.obs_names).fillna(0).astype(np.int32)
    if cache:  # the counts alone, written whole and then moved into place, so a reader never sees half a file
        tmp = f"{path}.{os.getpid()}.tmp"
        if meta.get("counts_from"):  # read back by a rebuild that finds the counts cached
            out.uns["counts_from"] = str(meta["counts_from"])
        out.write_h5ad(tmp)
        os.replace(tmp, path)
        out.uns = {}
    out.obs = cells.reindex(out.obs_names)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pertura.screen_object")
    ap.add_argument("--root", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extract-to")
    ap.add_argument("--parsed", help="the folder of the parsed text matrices")
    ap.add_argument("--cell-tables", help="the project's cell-table folder")
    a = ap.parse_args(argv)
    print(json.dumps(build(a.root, load_design(a.design), a.out, a.extract_to, a.parsed, a.cell_tables), indent=1,
                     default=str))


if __name__ == "__main__":
    main()
