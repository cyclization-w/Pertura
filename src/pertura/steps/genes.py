"""The genes step: each target's genes against its reference (docs/adr/0031, D1a).

The plumbing is here once:
- the reference by the design: where the design matches references to guide attributes
  (perturbation.reference_matches, docs/adr/0026), each target is tested on its arm's cells only, against its arm's
  reference, however many arms the targets given span;
- a target's own cells never in its reference ('other targets');
- one row shape for every backend: target, unit, gene, effect, p, q, test;
- the package calls;
- a calibration (every target a group of control guides, c.calibration_groups) kept beside the screen object, keyed by
  what its tests read: Pertura's code for them, and per group its test, its cells and its reference's. C.genes and
  each finding's checks then compute it once per build, test and reference; a rebuild's cells give a new key.

The backends are the field's packages, each a thin wrapper with its defaults:
- wilcoxon: scanpy's tl.rank_genes_groups on cells, Benjamini-Hochberg within each target;
- wilcoxon_same_samples: the same test, each target against the reference cells of its own samples in its mix
  (c.genes_wilcoxon_same_samples), so that a sample's difference is not read as the target's genes;
- edger_ql: edgeR's quasi-likelihood F-test on pseudobulk by unit (R), Benjamini-Hochberg within each target;
- pydeseq2: DESeq2's Wald test (pyDESeq2) on pseudobulk by unit, paired by unit (~ unit + condition), its own
  adjusted p within each target;
- mast: Seurat's FindMarkers with MAST (R), the sample as a latent variable;
- seurat_lr: Seurat's FindMarkers with its likelihood-ratio test (R), the sample as a latent variable;
- seurat3_lr: Seurat 3's likelihood-ratio test in Pertura's Python form, Benjamini-Hochberg within each target;
- limma_voom, deseq2, glmgampoi: limma's voom, R's DESeq2 (the original of pyDESeq2) or glmGamPoi on the edgeR
  backend's pseudobulk and design (~ unit + group, filterByExpr), Benjamini-Hochberg within each target;
- ttest: scanpy's tl.rank_genes_groups t-test on cells, Benjamini-Hochberg within each target;
- sceptre: SCEPTRE (R) against the non-targeting cells, its pair filters and resampling at their defaults.
The Seurat backends' q is made as the others' (Benjamini-Hochberg within the target), Seurat's own Bonferroni kept
as q_package, so the backends can be compared.

    table, calls = compare(screen, {target: {"method": ..., "reason": ...}}, "control_guides", workdir)
"""
import copy
import functools
import hashlib
import json
import os

import numpy as np
import pandas as pd

from .. import policy

COLUMNS = ["target", "unit", "gene", "effect", "p", "q", "test"]
EXTRA = ["q_package"]  # a backend's own adjusted p, where it has one beside q
ORDER = ("edger_ql", "limma_voom", "deseq2", "glmgampoi", "pydeseq2", "wilcoxon_same_samples", "wilcoxon", "ttest",
         "mast", "seurat_lr", "seurat3_lr", "sceptre")  # the order the backends' rows come in
BACKENDS = {}
# where only some genes are asked (a finding's): the tests whose p for a gene reads that gene alone run on those genes
# (on cells, each cell's whole library size kept)
CELL_LEVEL = ("wilcoxon_same_samples", "wilcoxon", "ttest", "seurat3_lr")
FEATURES = ("mast", "seurat_lr")  # Seurat's FindMarkers on its features, the cells normalised on every gene
# the others fit across genes (edgeR's and DESeq2's dispersions, SCEPTRE's resampling) and run on every gene


def backend(name, needs, q, samples=0, params=None, package=None):
    """Register a backend: needs is the layer it reads, q what its adjusted p means, samples how many samples must
    hold both a target and its reference for the test to be fitted (a target it cannot fit has no rows; for the
    others no row means no gene passed the package's own filters), params the package's settings a call may change:
    {name: (the package's default, what it does)}, and package a function returning the package's own function, whose
    signature's settings a call may change too (less those Pertura sets: SET_BY_PERTURA)."""
    def deco(fn):
        BACKENDS[name] = {"fn": fn, "needs": needs, "q": q, "samples": samples, "params": dict(params or {}),
                          "package": package}
        return fn
    return deco


# the package's settings Pertura sets itself for its comparison (the cells, the groups, the reference, the method)
SET_BY_PERTURA = {"adata", "groupby", "groups", "reference", "method", "key_added", "copy", "use_raw", "layer",
                  "mask_var", "tie_correct"}


def accepted(method):
    """The settings a call of this backend may change: those registered, and its package function's own."""
    names = set(BACKENDS[method]["params"])
    if BACKENDS[method].get("package"):
        import inspect
        try:
            names |= set(inspect.signature(BACKENDS[method]["package"]()).parameters) - SET_BY_PERTURA
        except (ImportError, ValueError, TypeError):
            pass
    names.discard("kwds")
    return names


def params_of(methods):
    """The settings a call of these backends may change, with their defaults: {backend: {name: (default, what)}}."""
    return {m: BACKENDS[m]["params"] for m in sorted(methods)}


def check_params(methods, params):
    """Refuse a setting none of these backends has, naming theirs: a setting that would change nothing is refused,
    not ignored."""
    allowed = {p for m in methods for p in accepted(m)}
    unknown = sorted(set(params or {}) - allowed)
    if unknown:
        have = {m: sorted(accepted(m)) for m in sorted(methods)}
        raise ValueError(f"no setting {unknown} for {sorted(methods)}; their settings are {have}")


def fitted(method, table, targets, calls=()):
    """The targets a backend's table holds a fitted test for: all of them but those its package calls name as not
    fitted, and, where the backend needs samples holding both sides, those without rows."""
    failed = {t for c in calls or () for t in c.get("not_fitted", ())}
    have = set(table["target"])
    needs = BACKENDS.get(method, {}).get("samples")
    return [t for t in targets if t not in failed and (not needs or t in have)]


def bh_over(p, m):
    """Benjamini-Hochberg over m tests of which these p were computed: the genes a package's own filter left
    untested (Seurat's min.pct and logfc.threshold) count as p = 1, so the filter does not make q smaller."""
    from ..question_modules import c
    p = np.asarray(p, float)
    return c.bh(np.concatenate([p, np.ones(max(0, int(m) - len(p)))]))[:len(p)]


def _seurat_rows(screen, table, test, features=None):
    """The rows of a Seurat backend: q by Benjamini-Hochberg within the target over every gene of the screen (over
    the features, where only some were tested), beside Seurat's own adjusted p (Bonferroni) as q_package."""
    parts = []
    m = len(features) if features else len(screen.genes)
    for t, part in table.groupby("target", sort=False):
        parts.append(part.assign(q_package=part["q"], q=bh_over(part["p"].to_numpy(), m), test=test))
    return pd.concat(parts, ignore_index=True) if parts else table.assign(q_package=[], test=[])


def unmatched(screen):
    """A screen cut to one arm, its reference matching already done (so its tests are not split again)."""
    s = copy.copy(screen)
    s.__dict__["reference_matches"] = []
    return s


def arms(screen, targets, reference):
    """[(arm key, its targets, the screen cut to its targets' cells and its reference)] where the design matches
    references to attributes; one part with the whole screen otherwise. A call of one arm's targets is cut too: the
    whole screen's reference holds every arm's controls."""
    if not getattr(screen, "reference_matches", None) or reference == "no_guide":
        return [("", list(targets), screen)]
    by = {}
    for x in targets:  # a target whose guides span arms is refused (BR26); a combination of two arms is 'every arm'
        by.setdefault(screen.arm_or_every(x), []).append(x)
    out = []
    for key, members in sorted(by.items()):
        ref = screen.reference(reference) if key == "every arm" else screen.reference(reference, target=members[0])
        out.append((key, members, unmatched(screen.subset(cells=np.isin(screen.label, members) | ref))))
    return out


CACHE = "calibrations"  # the folder of kept calibrations, in the screen object's folder


@functools.lru_cache(maxsize=1)
def code_version():
    """Pertura's code for the gene tests: this step, the modules' test code and the R scripts, hashed."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    files = [os.path.join(here, "genes.py"), os.path.join(root, "question_modules", "c.py")]
    rdir = os.path.join(root, "r")
    files += sorted(os.path.join(rdir, f) for f in os.listdir(rdir) if f.endswith(".R")) if os.path.isdir(rdir) else []
    h = hashlib.sha256()
    for f in files:
        with open(f, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def fingerprint(screen, leads, reference, params=None, genes=None):
    """What a call's table depends on: the tests' code, the genes and settings, and per target its test, its cells
    and its reference's cells (their barcodes, samples and library sizes)."""
    h = hashlib.sha256(code_version().encode())
    names = np.asarray(screen.adata.obs_names).astype(str)
    h.update(json.dumps([reference, params or {}, genes, hashlib.sha256("\n".join(screen.genes).encode()).hexdigest()],
                        sort_keys=True, default=str).encode())
    for t in sorted(leads):
        tm = screen.cells_of(t)
        rm = (screen.single() if reference == "other_targets" else screen.reference(reference, target=t)) & ~tm
        h.update(f"{t}\t{leads[t]['method']}\n".encode())
        for m in (tm, rm):
            idx = np.flatnonzero(m)
            h.update("\n".join(names[idx]).encode())
            # their samples, and their conditions where a sample holds several (the draw of control cells reads them)
            h.update("\n".join(np.asarray(screen.strata)[idx]).encode())
            h.update(np.asarray(screen.libsize, dtype=float)[idx].tobytes())
    return h.hexdigest()[:32]


def _kept(screen, key):
    folder = os.path.join(getattr(screen, "dir", "") or "", CACHE, key)
    try:
        table = pd.read_csv(os.path.join(folder, "table.tsv.gz"), sep="\t", dtype={"target": str, "gene": str})
        with open(os.path.join(folder, "calls.json"), encoding="utf-8") as fh:
            calls = json.load(fh)
    except (OSError, ValueError):
        return None
    return table, [{**c, "calibration": "computed once for this build, test and reference, and reused"}
                   for c in calls]


def _keep(screen, key, table, calls):
    """The calibration's table beside the screen object; where that folder cannot be written (a confined notebook),
    it is computed again next time."""
    folder = os.path.join(getattr(screen, "dir", "") or "", CACHE, key)
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, f"table.{os.getpid()}.tmp")
        table.to_csv(tmp, sep="\t", index=False, compression="gzip")
        os.replace(tmp, os.path.join(folder, "table.tsv.gz"))
        with open(os.path.join(folder, f"calls.{os.getpid()}.tmp"), "w", encoding="utf-8") as fh:
            json.dump(calls, fh, default=str)
        os.replace(os.path.join(folder, f"calls.{os.getpid()}.tmp"), os.path.join(folder, "calls.json"))
    except OSError:
        pass


def compare(screen, leads, reference, workdir, workers=None, params=None, genes=None):
    """Each target by its backend ({target: {method, reason}} or {target: method}): one table of every row, and the
    package calls, each call naming the arm it ran on. params: package settings changed from their defaults, each
    given to the backends that have it. genes: only these genes' rows (a finding's), q by Benjamini-Hochberg over
    them; a test on cells or Seurat's then tests them alone, the others still fit on every gene."""
    if genes is not None:
        genes = [g for g in dict.fromkeys(map(str, genes)) if g in screen.gene_index]
        if not genes:  # none of them is a gene of the matrix: nothing to test
            return pd.DataFrame(columns=COLUMNS), []
    leads ={t: (ld if isinstance(ld, dict) else {"method": ld}) for t, ld in leads.items()}
    unknown = sorted({ld["method"] for ld in leads.values()} - set(BACKENDS))
    if unknown:
        raise ValueError(f"no genes backend {unknown}; the backends are {sorted(BACKENDS)}")
    params = dict(params or {})
    check_params({ld["method"] for ld in leads.values()}, params)
    from ..question_modules.c import CALIBRATION_PREFIX
    kept_as = None  # a calibration's key, where the call is one
    if leads and getattr(screen, "dir", None) and all(str(t).startswith(CALIBRATION_PREFIX) for t in leads):
        kept_as = fingerprint(screen, leads, reference, params, genes)
        got = _kept(screen, kept_as)
        if got is not None:
            return got
    parts, calls = [], []
    split = arms(screen, list(leads), reference)
    for k, (key, members, s) in enumerate(split):
        folder = os.path.join(workdir, f"arm_{k}") if len(split) > 1 or key else workdir
        for method in ORDER:
            ts = [x for x in members if leads[x]["method"] == method]
            if not ts:
                continue
            own = {k: v for k, v in params.items() if k in accepted(method)}
            here = s
            if genes is not None and method in CELL_LEVEL:
                here = s.with_genes(genes)
            elif genes is not None and method in FEATURES:
                own["features"] = genes
            table, call = BACKENDS[method]["fn"](here, ts, reference, os.path.join(folder, method), workers, **own)
            keep = COLUMNS + [x for x in EXTRA if x in table.columns]
            parts.append(table[keep] if set(COLUMNS) <= set(table.columns) else table)
            for c in call:
                words = ", ".join(json.loads(key)) if str(key).startswith("[") else key  # the arm in words
                calls.append({**c, "contrast": f"{c.get('contrast', '')} (the arm {words})".strip()} if key else c)
    table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
    if genes is not None:  # the genes asked, their q over them alone
        table = table[table["gene"].astype(str).isin(genes)].copy()
        for _, idx in table.groupby("target", sort=False).groups.items():
            table.loc[idx, "q"] = bh_over(table.loc[idx, "p"].to_numpy(dtype=float), len(genes))
        calls = [{**c, "genes": f"only {', '.join(genes[:5])}{', ...' if len(genes) > 5 else ''}, q over them"}
                 for c in calls]
    if kept_as is not None:
        _keep(screen, kept_as, table, calls)
    return table, calls


@backend("pydeseq2", needs="raw counts", q="DESeq2's Benjamini-Hochberg within the target (padj; a gene DESeq2's "
         "independent filtering or outlier rule leaves without one counts as not changed)", samples=3,
         params={"min_cells": (10, "a side's cells in a sample for the sample to count")})
def _pydeseq2(screen, targets, reference, workdir, workers, min_cells=10):
    """Each target against its reference in the samples both hold with min_cells cells (3 or more), pseudobulk sums
    by sample (tools.de.pseudobulk.aggregate, genes in 10% of the samples), DESeq2's design ~ unit + condition, the
    contrast target against reference; one process (n_cpus 1)."""
    import importlib.metadata
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats
    from ..question_modules import packages
    from ..question_modules.data import seed_of
    from ..tools.de.pseudobulk import aggregate
    packages.check_layer(screen.X, "raw_counts", "pyDESeq2 (pseudobulk of raw counts)")
    parts = []
    for t in targets:
        tm = screen.cells_of(t)
        rm = screen.reference(reference, target=t) & ~tm
        if screen.crossed:  # its control cells drawn within its samples' conditions, in its mix
            tm, rm, _ = screen.matched(tm, rm, min_cells, seed_of(t))
        mat, meta = aggregate(screen.X, {"reference": np.flatnonzero(rm), "target": np.flatnonzero(tm)}, screen.unit,
                              screen.genes, min_cells, None, True)
        if meta.empty:
            continue
        shared = sorted(set(meta.loc[meta["group"] == "target", "mouse"]) &
                        set(meta.loc[meta["group"] == "reference", "mouse"]))
        if len(shared) < policy.floor("min_replicates", "method"):  # too few samples hold both sides
            continue
        if len(shared) < policy.get("min_replicates"):
            policy.note(f"pydeseq2 ran with {len(shared)} replicates holding both sides for {t}; Pertura's default for "
                        f"a test over replicates needs {policy.get('min_replicates')}")
        meta = meta[meta["mouse"].isin(shared)]
        counts = mat[meta["sample"].to_numpy()].T
        design = pd.DataFrame({"unit": meta["mouse"].astype(str).to_numpy(),
                               "condition": meta["group"].to_numpy()}, index=counts.index)
        dds = DeseqDataSet(counts=counts, metadata=design, design="~unit + condition", n_cpus=1, quiet=True)
        dds.deseq2()
        st = DeseqStats(dds, contrast=["condition", "target", "reference"], n_cpus=1, quiet=True)
        st.summary()
        r = st.results_df
        parts.append(pd.DataFrame({"target": t, "unit": "all", "gene": r.index.astype(str),
                                   "effect": r["log2FoldChange"].to_numpy(), "p": r["pvalue"].to_numpy(),
                                   "q": r["padj"].fillna(1.0).to_numpy(), "test": "pydeseq2"}))
    table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
    call = packages.call("pydeseq2", "DeseqDataSet, DeseqStats", data=screen.X, layer="raw_counts",
                         formula="~ unit + condition", contrast=(
                             "each target against its control cells drawn from its own samples and conditions, in its "
                             "mix, in the samples both hold" if screen.crossed else
                             "each target against its reference, in the samples both hold"),
                         covariates=["sample or lane"], min_cells_per_sample=min_cells,
                         filter="genes in 10% of samples")
    call["version"] = importlib.metadata.version("pydeseq2")
    return table, [call]


WILCOXON_Q = "Benjamini-Hochberg within the target, over the genes detected in 10% of the cells on one side"


def _rank_genes_groups():
    import scanpy as sc
    return sc.tl.rank_genes_groups


@backend("wilcoxon", needs="raw counts (log-normalised here)", q=WILCOXON_Q,
         package=_rank_genes_groups)
def _wilcoxon(screen, targets, reference, workdir, workers, **settings):
    from ..question_modules import c
    table, call = c.genes_wilcoxon(screen, targets, reference, workers=workers, settings=settings)
    return table, [call] if call else []


@backend("wilcoxon_same_samples", needs="raw counts (log-normalised here) and each cell's sample",
         q=WILCOXON_Q, package=_rank_genes_groups)
def _wilcoxon_same_samples(screen, targets, reference, workdir, workers, **settings):
    from ..question_modules import c
    table, call = c.genes_wilcoxon_same_samples(screen, targets, reference, workers=workers, settings=settings)
    return table, [call] if call else []


@backend("edger_ql", needs="raw counts", q="Benjamini-Hochberg within the target", samples=3,
         params={"min_cells": (10, "a side's cells in a sample for the sample to count")})
def _edger(screen, targets, reference, workdir, workers, min_cells=10):
    from ..question_modules import c
    if reference != "other_targets":
        table, _, call = c.genes_edger(screen, targets, reference, workdir, min_cells=min_cells)
        return table.assign(test="edger_ql"), [call]
    parts, call = [], None
    for k, x in enumerate(targets):  # each target against the others' cells, its own left out
        lab = np.where(screen.cells_of(x), x, np.where(screen.single(), "control", "none"))
        table, _, call = c.genes_edger(screen.with_labels(lab), [x], "control_guides", os.path.join(workdir, f"{k}"),
                                       min_cells=min_cells)
        parts.append(table.assign(test="edger_ql"))
    call = {**call, "contrast": "each target against the other targets' cells, in the samples they share"}
    return pd.concat(parts, ignore_index=True), [call]


def _pseudobulk(tool, name):
    """A pseudobulk backend on the edgeR backend's code (c.genes_edger with the tool)."""
    def run(screen, targets, reference, workdir, workers, min_cells=10):
        from ..question_modules import c
        if reference != "other_targets":
            table, _, call = c.genes_edger(screen, targets, reference, workdir, min_cells=min_cells, tool=tool)
            return table.assign(test=name), [call]
        parts, call = [], None
        for k, x in enumerate(targets):  # each target against the others' cells, its own left out
            lab = np.where(screen.cells_of(x), x, np.where(screen.single(), "control", "none"))
            table, _, call = c.genes_edger(screen.with_labels(lab), [x], "control_guides",
                                           os.path.join(workdir, f"{k}"), min_cells=min_cells, tool=tool)
            parts.append(table.assign(test=name))
        call = {**call, "contrast": "each target against the other targets' cells, in the samples they share"}
        return pd.concat(parts, ignore_index=True), [call]
    return run


for _tool, _name in (("limma", "limma_voom"), ("deseq2", "deseq2"), ("glmgampoi", "glmgampoi")):
    backend(_name, needs="raw counts", q="Benjamini-Hochberg within the target", samples=3,
            params={"min_cells": (10, "a side's cells in a sample for the sample to count")})(_pseudobulk(_tool, _name))


@backend("ttest", needs="raw counts (log-normalised here)", q=WILCOXON_Q,
         package=_rank_genes_groups)
def _ttest(screen, targets, reference, workdir, workers, **settings):
    from ..question_modules import c
    table, call = c.genes_wilcoxon(screen, targets, reference, workers=workers, method="t-test", settings=settings)
    return table, [call] if call else []


@backend("sceptre", needs="raw counts and each cell's guide", q="Benjamini-Hochberg within the target over the genes "
         "SCEPTRE's pair filters keep; SCEPTRE's own family (every pair) as q_package",
         params={"n_nonzero_trt_thresh": (7, "SCEPTRE's: a pair's non-zero cells needed among the target's cells"),
                 "n_nonzero_cntrl_thresh": (7, "SCEPTRE's: a pair's non-zero cells needed among the control cells"),
                 "side": ("both", "SCEPTRE's sidedness: both, left or right"),
                 "resampling_approximation": ("skew_normal", "SCEPTRE's: skew_normal, or no_approximation (the "
                                                             "resampled statistics themselves)")})
def _sceptre(screen, targets, reference, workdir, workers, **params):
    from ..question_modules import c
    table, call = c.genes_sceptre(screen, targets, reference, workdir, **params)
    return table, [call]


SEURAT = {"logfc_threshold": (0.1, "Seurat's: genes whose log2 fold change is below it are not tested"),
          "min_pct": (0.01, "Seurat's: genes detected in less of each side's cells are not tested"),
          "max_cells_per_ident": (None, "Seurat's cap on each side's cells (none: every cell)"),
          "cell_cycle": (False, "the cells' S and G2/M scores (steps.cell_cycle, scanpy) as latent variables "
                                "beside the sample")}


@backend("mast", needs="raw counts", q="Benjamini-Hochberg within the target over every gene (genes Seurat's filters "
         "left untested count as p = 1); Seurat's own adjusted p (Bonferroni) as q_package", params=SEURAT)
def _mast(screen, targets, reference, workdir, workers, logfc_threshold=0.1, min_pct=0.01, max_cells_per_ident=None,
          cell_cycle=False, features=None):
    from ..question_modules import c
    os.makedirs(workdir, exist_ok=True)
    table, _, calls, _, _ = c.genes_field(screen, screen.design, targets, reference, workdir, calibrate=False,
                                          max_cells=max_cells_per_ident, logfc_threshold=logfc_threshold,
                                          min_pct=min_pct, cell_cycle=cell_cycle, features=features)
    return _seurat_rows(screen, table, "mast", features), list(calls)


@backend("seurat_lr", needs="raw counts (log-normalised by Seurat)", q="Benjamini-Hochberg within the target over "
         "every gene (genes Seurat's filters left untested count as p = 1); Seurat's own adjusted p (Bonferroni) as "
         "q_package", params=SEURAT)
def _seurat_lr(screen, targets, reference, workdir, workers, logfc_threshold=0.1, min_pct=0.01,
               max_cells_per_ident=None, cell_cycle=False, features=None):
    """Seurat's FindMarkers(test.use = "LR") in R, with the sample as a latent variable where the cells span two or
    more samples, the two sides compared within the samples both hold."""
    from ..question_modules import c
    os.makedirs(workdir, exist_ok=True)
    table, _, calls, _, _ = c.genes_field(screen, screen.design, targets, reference, workdir, calibrate=False,
                                          test="LR", max_cells=max_cells_per_ident, logfc_threshold=logfc_threshold,
                                          min_pct=min_pct, cell_cycle=cell_cycle, features=features)
    return _seurat_rows(screen, table, "seurat_lr", features), list(calls)


@backend("seurat3_lr", needs="log-normalised values", q="Benjamini-Hochberg within the target")
def _seurat3_lr(screen, targets, reference, workdir, workers, **_):
    from ..question_modules import c
    parts = []
    for t in targets:
        mt = screen.cells_of(t)
        rm = screen.reference(reference, target=t) & ~mt
        # the conditions inside the samples as a latent variable, where a sample holds several
        de = c.seurat3_lr_de(screen, mt, rm, c.with_conditions(screen, None, mt | rm))
        de = de[de["tested"]]
        parts.append(pd.DataFrame({"target": t, "unit": "all", "gene": de["gene"], "effect": de["avg_logFC"],
                                   "p": de["p_val"], "q": c.bh(de["p_val"].to_numpy()), "test": "seurat3_lr"}))
    table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
    return table, [{"package": "pertura", "function": "Seurat 3's likelihood-ratio test (FindMarkers test.use LR), "
                    "its Python form", "layer": "lognorm",
                    **({"covariates": ["condition"]} if screen.crossed else {})}]
