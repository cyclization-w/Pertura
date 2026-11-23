"""The enrichment step: gene sets over-represented in a list of genes (ORA), or enriched at the ends of a ranking
(GSEA) (docs/adr/0031, phase 2; about 45 of 75 published screens test enrichment).

The backends, each with its package's defaults:
- gseapy: GSEApy's enrich (Enrichr's hypergeometric test against the universe given, Benjamini-Hochberg over the sets
  the list touches) and prerank (min_size 15, max_size 500, 1,000 permutations);
- clusterprofiler: clusterProfiler's enricher and GSEA (fgsea inside; minGSSize 10, maxGSSize 500, BH), in R
  (pertura/r/enrich_clusterprofiler.R). enricher keeps only the genes of the list and of the universe that some set
  holds, its documented rule, so its p differs from GSEApy's on the same input.
Each backend's known case is checked under its own rule before a result is given (`checked`).

    table = ora(genes, universe, sets, backend="gseapy")
    table = gsea(ranking, sets, backend="clusterprofiler", seed=0)
"""
import os
import tempfile

import numpy as np
import pandas as pd

BACKENDS = ("gseapy", "clusterprofiler")
ORA_COLUMNS = ["set", "overlap", "set_size", "odds_ratio", "p", "q", "genes", "backend"]
GSEA_COLUMNS = ["set", "nes", "p", "q", "lead_genes", "set_size", "backend"]
SIZES = {"gseapy": (15, 500), "clusterprofiler": (10, 500)}  # each package's GSEA set sizes by default
ORA_SIZES = {"gseapy": (1, 100000), "clusterprofiler": (10, 500)}  # Enrichr takes every set the list touches


def sets_for(collection, organism, universe=None, gene_lists=None):
    """{set: genes}: an MSigDB collection kept offline (pertura.gene_sets.msigdb) for the organism, or the lists
    given; each set cut to the universe where one is given, sets with no gene left out."""
    from .. import gene_sets as gs
    found = dict(gene_lists) if gene_lists else gs.msigdb(collection, organism)
    if universe is not None:
        universe = set(universe)
        found = {k: sorted(set(v) & universe) for k, v in found.items()}
    return {k: sorted(set(v)) for k, v in found.items() if v}


ACTIVITY_COLUMNS = ["target", "source", "activity", "q", "genes_tested"]
RESOURCES = ("collectri", "progeny")  # weighted networks, read by activity only


def network(collection, organism, gene_lists=None):
    """A long table (source, target, weight) for decoupler: CollecTRI's regulons, PROGENy's pathways (its 500 top
    genes, decoupler's default), or a collection's sets or the lists given, each gene weighted 1."""
    from .. import gene_sets as gs
    if collection == "collectri" and not gene_lists:
        return gs.collectri(organism)
    if collection == "progeny" and not gene_lists:
        return gs.progeny(organism, top=500)[["source", "target", "weight"]]
    sets = sets_for(collection, organism, None, gene_lists)
    return pd.DataFrame([(s, g, 1.0) for s, genes in sets.items() for g in genes],
                        columns=["source", "target", "weight"])


def signed_statistic(part):
    """Each gene's signed test statistic: the table's own (stat), else the z of its two-sided p with the sign of its
    effect, as decoupler's tutorials read a contrast by its test statistic; a sparse gene's extreme log fold change
    with a weak test then weighs little (on GSE278572, 15% of the tested genes had |log2 fold change| > 10). The effect
    itself where the table has no p."""
    from scipy import stats
    if "stat" in part.columns:
        return pd.to_numeric(part["stat"], errors="coerce").to_numpy(dtype=float)
    eff = pd.to_numeric(part["effect"], errors="coerce").to_numpy(dtype=float)
    if "p" not in part.columns:
        return eff
    p = np.clip(pd.to_numeric(part["p"], errors="coerce").to_numpy(dtype=float), 1e-300, 1.0)
    return np.sign(eff) * stats.norm.isf(p / 2)


def one_pair_each(net):
    """ValueError where a network repeats a (source, target) pair, which decoupler refuses."""
    twice = net[net.duplicated(subset=["source", "target"], keep=False)]
    if len(twice):
        raise ValueError(f"the network repeats {twice[['source', 'target']].drop_duplicates().shape[0]} (source, "
                         f"target) pairs, such as {twice[['source', 'target']].head(2).values.tolist()}: give one "
                         "row per pair (gene_sets.one_row_per_pair)")


def activities(table, net, tmin=5):
    """Each target's activity of each source from its genes' changes, as decoupler's tutorials read factors and
    pathways: its univariate linear model (ulm) on the target's signed test statistics over the genes tested for it
    (signed_statistic), a gene not tested being no zero change; q is decoupler's Benjamini-Hochberg over the sources
    within the target. table: target, gene, effect, p; net: source, target, weight. Returns ACTIVITY_COLUMNS."""
    one_pair_each(net)  # decoupler asserts this inside the loop below, where a failure reads as too few genes
    import decoupler as dc
    rows = []
    for t, part in table.groupby(table["target"].astype(str), sort=False):
        val = signed_statistic(part)
        keep = np.isfinite(val)
        if not keep.any():
            continue
        data = pd.DataFrame([val[keep]], index=[t], columns=part["gene"].astype(str).to_numpy()[keep])
        data = data.loc[:, ~data.columns.duplicated()]
        try:
            es, padj = dc.mt.ulm(data=data, net=net, tmin=tmin)
        except (ValueError, AssertionError):  # no source keeps tmin of its genes here (decoupler asserts it)
            continue
        rows += [{"target": t, "source": str(src), "activity": float(es.at[t, src]), "q": float(padj.at[t, src]),
                  "genes_tested": int(data.shape[1])} for src in es.columns]
    return pd.DataFrame(rows, columns=ACTIVITY_COLUMNS)


def activity_band(calibration, net, tmin=5):
    """{source: the 95th percentile of the control-guide groups' absolute activities}: the same ulm on each group's
    log2 fold changes (the gene test's calibration, its groups as the targets)."""
    act = activities(calibration, net, tmin)
    return {str(s): float(np.percentile(np.abs(v), 95)) for s, v in act.groupby("source")["activity"]}


def ora(genes, universe, sets, backend="gseapy", workdir=None):
    """The over-representation of `genes` among `universe`, per set. Returns ORA_COLUMNS."""
    if backend not in BACKENDS:
        raise ValueError(f"backend is one of {list(BACKENDS)}, not {backend!r}")
    universe = set(universe)
    hits = sorted(set(genes) & universe)
    if not hits:
        return pd.DataFrame(columns=ORA_COLUMNS)
    if backend == "gseapy":
        import gseapy
        r = gseapy.enrich(gene_list=hits, gene_sets=sets, background=sorted(universe), outdir=None, no_plot=True,
                          cutoff=1.0).results
        if not isinstance(r, pd.DataFrame) or r.empty:  # no set holds any of the genes: gseapy gives an empty list
            return pd.DataFrame(columns=ORA_COLUMNS)
        sizes = {k: len(v) for k, v in sets.items()}
        return pd.DataFrame({"set": r["Term"], "overlap": r["Overlap"].str.split("/").str[0].astype(int),
                             "set_size": r["Term"].map(sizes), "odds_ratio": r["Odds Ratio"].astype(float),
                             "p": r["P-value"].astype(float), "q": r["Adjusted P-value"].astype(float),
                             "genes": r["Genes"].str.replace(";", ","), "backend": backend})[ORA_COLUMNS]
    r = _clusterprofiler("ora", sets, workdir, lists={"list": hits}, universe=sorted(universe))
    if r.empty:
        return pd.DataFrame(columns=ORA_COLUMNS)
    return pd.DataFrame({"set": r["ID"], "overlap": r["Count"].astype(int),
                         "set_size": r["BgRatio"].astype(str).str.split("/").str[0].astype(int),
                         "odds_ratio": np.nan, "p": r["pvalue"].astype(float), "q": r["p.adjust"].astype(float),
                         "genes": r["geneID"].astype(str).str.replace("/", ","), "backend": backend})[ORA_COLUMNS]


def gsea(ranking, sets, backend="gseapy", workdir=None, seed=0, permutations=1000):
    """Gene sets at the ends of a ranking (a Series: gene, statistic). Returns GSEA_COLUMNS."""
    if backend not in BACKENDS:
        raise ValueError(f"backend is one of {list(BACKENDS)}, not {backend!r}")
    rank = pd.Series(ranking, dtype=float).dropna().sort_values(ascending=False)
    rank = rank[~rank.index.duplicated()]
    lo, hi = SIZES[backend]
    if backend == "gseapy":
        import gseapy
        pre = gseapy.prerank(rnk=rank, gene_sets=sets, min_size=lo, max_size=hi, permutation_num=permutations,
                             seed=seed, threads=1, outdir=None, no_plot=True)
        r = pre.res2d
        sizes = {k: len(set(v) & set(rank.index)) for k, v in sets.items()}
        return pd.DataFrame({"set": r["Term"], "nes": r["NES"].astype(float), "p": r["NOM p-val"].astype(float),
                             "q": r["FDR q-val"].astype(float), "lead_genes": r["Lead_genes"],
                             "set_size": r["Term"].map(sizes), "backend": backend})[GSEA_COLUMNS]
    r = _clusterprofiler("gsea", sets, workdir, ranking=rank, seed=seed)
    if r.empty:
        return pd.DataFrame(columns=GSEA_COLUMNS)
    return pd.DataFrame({"set": r["ID"], "nes": r["NES"].astype(float), "p": r["pvalue"].astype(float),
                         "q": r["p.adjust"].astype(float), "lead_genes": r["core_enrichment"].astype(str)
                         .str.replace("/", ";"), "set_size": r["setSize"].astype(int),
                         "backend": backend})[GSEA_COLUMNS]


def _clusterprofiler(method, sets, workdir, lists=None, universe=None, ranking=None, seed=0):
    """clusterProfiler in R on files in a work folder: its result table."""
    import subprocess
    from .. import envs
    from ..question_modules.c import R_SLOTS
    base = tempfile.mkdtemp(dir=workdir) if workdir else tempfile.mkdtemp()
    pd.DataFrame([(k, g) for k, v in sets.items() for g in v], columns=["term", "gene"]).to_csv(
        os.path.join(base, "sets.tsv"), sep="\t", index=False)
    if method == "ora":
        pd.DataFrame([(k, g) for k, v in lists.items() for g in v], columns=["list", "gene"]).to_csv(
            os.path.join(base, "lists.tsv"), sep="\t", index=False)
        with open(os.path.join(base, "universe.txt"), "w", encoding="utf-8") as fh:
            fh.write("".join(f"{g}\n" for g in universe))
        lo, hi = ORA_SIZES["clusterprofiler"]
    else:
        pd.DataFrame({"gene": ranking.index.astype(str), "stat": ranking.to_numpy()}).to_csv(
            os.path.join(base, "ranking.tsv"), sep="\t", index=False)
        lo, hi = SIZES["clusterprofiler"]
    with R_SLOTS:
        p = subprocess.run([envs.rscript(), os.path.join(R_DIR, "enrich_clusterprofiler.R"), method, str(lo), str(hi),
                            str(int(seed))], env=envs.r_env({"PERTURA_ENRICH_DIR": base, "OMP_NUM_THREADS": "1"}),
                           capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"clusterProfiler failed: {p.stderr[-1500:]}")
    import shutil
    path = os.path.join(base, "result.tsv")
    try:
        found = pd.read_csv(path, sep="\t", dtype={"ID": str}) if os.path.getsize(path) else pd.DataFrame()
    except (FileNotFoundError, OSError, pd.errors.EmptyDataError):
        found = pd.DataFrame()
    shutil.rmtree(base, ignore_errors=True)  # the call's own work files
    return found


def checked(backend, workdir=None):
    """Each backend's test on a known case, under its own rule: 10 of 15 genes in set S (10 genes), none in R (10
    genes), among 200. GSEApy gives the hypergeometric over the universe given (1.34e-13); clusterProfiler over the
    genes some set holds (20), with the list cut to those (10 of 10). Raises where a version changes it."""
    from scipy import stats
    genes = [f"g{i}" for i in range(200)]
    r = ora(genes[:15], set(genes), {"S": genes[:10], "R": genes[100:110]}, backend, workdir).set_index("set")
    expect = (stats.hypergeom.sf(9, 200, 10, 15) if backend == "gseapy" else stats.hypergeom.sf(9, 20, 10, 10))
    if "S" not in r.index or not np.isclose(r.at["S", "p"], expect, rtol=1e-6) or \
            ("R" in r.index and r.at["R", "p"] < 1 and backend == "gseapy"):
        raise RuntimeError(f"{backend}'s enrichment test no longer gives the hypergeometric p of its own rule on a "
                           "known case; pertura/steps/enrichment.py needs revisiting before enrichment is reported")
    return float(r.at["S", "p"])


R_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "r")
