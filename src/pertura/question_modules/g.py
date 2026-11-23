"""Question G: what the findings mean, from outside knowledge. Everything a G module returns is prior knowledge,
kept apart from the evidence in the data: the analysis server tags it so in the record.

The tests are GSEApy's (Enrichr's over-representation test and prerank GSEA, in 17 of 75 published screens as
Enrichr or GSEApy; docs/build/adr0022/published_screens_on_our_choices_and_ideas.md): a gene list against the genes
the screen detects (its hypergeometric test, Benjamini-Hochberg over the sets the list touches), up and down genes
apart (as 5 of the papers do), and a ranked list by prerank. _enrich_checked runs the test on a known case first, so a
version that changes it fails loudly instead of answering wrongly.
"""
import os

import numpy as np
import pandas as pd

from ..gene_sets import NAMED
from . import figures, packages
from .contract import Contract, Result, register
from .data import value

GSEA_SIZES = (15, 500)  # GSEApy's prerank defaults: sets with fewer or more genes in the ranking are left out


def _sets(collection, organism, universe):
    """{set: [its genes the screen detects]}, sets with no such gene left out."""
    from .. import gene_sets as gs
    out = {name: sorted(set(members) & universe) for name, members in gs.msigdb(collection, organism).items()}
    return {k: v for k, v in out.items() if v}


def _enrich(genes, universe, sets, backend="gseapy", workdir=None):
    """The enrichment step's over-representation test of `genes` against `universe` (pertura.steps.enrichment)."""
    from ..steps import enrichment
    return enrichment.ora(genes, universe, sets, backend, workdir)


def _enrich_checked(backend="gseapy", workdir=None):
    """The backend's test on a known case, under its own rule (pertura.steps.enrichment.checked)."""
    from ..steps import enrichment
    return enrichment.checked(backend, workdir)


@register("G.meaning", Contract(
    serves="G.meaning",
    applies_when="always",
    needs="the up and down genes of a finding (or one list) for the over-representation test, or a ranking of genes "
          "(a target's effects) for GSEA, or a genes run for each target's own genes; the organism (experiment.organism) for the collection; the screen's "
          "detected genes as the background",
    returns="per gene set and direction: GSEApy's over-representation test (Enrichr's hypergeometric test against "
            "the genes the screen detected in 1% of cells, Benjamini-Hochberg), the overlap, the gene ratio "
            "(overlap over the list's genes) and the genes; or prerank GSEA's normalised enrichment score and FDR on "
            "the ranking; with a genes run (de), the same for each target: ORA of its changed genes, up and down "
            "apart, against the genes tested for it, or GSEA on its genes ranked by their signed test statistics, as "
            "published screens read each perturbation's changed genes; or (method ulm, on a genes run: de) per target "
            "and source, the source's activity from the "
            "target's signed test statistics over its tested genes (the z of each gene's p with its effect's sign; "
            "decoupler's univariate linear model, as its tutorials "
            "read factors with CollecTRI and pathways with PROGENy), q its BH over the sources within the target, "
            "beside the control-guide groups' line (the 95th percentile of their absolute activities from the same "
            "gene test's calibration); the package call",
    fails_when=[("a whole-genome background inflates enrichment",
                 "the background is the genes the screen detected, stated with the result"),
                ("up and down genes pooled hide sets that go one way", "up and down genes are tested apart"),
                ("enrichment names a pathway, not a mechanism in these cells",
                 "the result is prior knowledge, tagged so in the record, and never changes a finding")],
    tested_against="a known case run before every test (10 of 15 genes in one set among 200: the hypergeometric "
                   "p), and a planted set found first on the synthetic screen (tests/test_question_modules.py)",
    choices={"collection": [*NAMED, "collectri", "progeny"], "method": ["ora", "gsea", "ulm"],
             "backend": ["gseapy", "clusterprofiler"]},
    inputs={"de": "a C.genes run, by its ID or <run>:changed_genes (the run's own test of every gene is read): with ora "
                  "each target's changed genes, up and down apart, against the genes tested for it; with gsea each "
                  "target's genes ranked by their signed test statistics; with ulm each target's activities, beside "
                  "its calibration for the control-guide groups' line; or a genes table (target, gene, effect, and "
                  "changed or q)",
            "calibration": "ulm: the control-guide groups' genes table of the same test (target the group, gene, "
                           "effect), where de is a table; read from the run where de names one",
            "targets": "the targets of de to read (default: every target of de)",
            "up": "ORA: the genes that go up (a list, or a table with a gene column)",
            "down": "ORA: the genes that go down (a list, or a table with a gene column)",
            "genes": "ORA: one list, where the direction is not given (a list, or a table with a gene column)",
            "ranking": "GSEA: {gene: statistic}, every ranked gene, or a table of one ranking (gene, and stat, score "
                       "or effect)",
            "gene_lists": "{set: [genes]} in place of a collection: a lab's own sets, another library's (any "
                          "organism)",
            "universe": "the genes tested against, such as the genes a test tested (as pt.enrich takes them); by "
                        "default the genes the screen detected in 1% of cells"},
    scope={"genes": ("method", ["ora"]), "up": ("method", ["ora"]), "down": ("method", ["ora"]),
           "ranking": ("method", ["gsea"]), "calibration": ("method", ["ulm"])}))
def meaning(screen, design, out_dir, collection="hallmark", method="ora", genes=None, up=None, down=None,
            ranking=None, seed=0, backend="gseapy", gene_lists=None, universe=None, de=None, calibration=None,
            targets=None):
    from .. import policy
    from ..steps.enrichment import RESOURCES
    path = os.path.join(out_dir, "figure.png")
    organism = value(design, "experiment.organism")
    if method == "ulm":
        return activities(screen, design, out_dir, collection, gene_lists, de, calibration, targets)
    if collection in RESOURCES and not gene_lists:
        checks = figures.bars(np.zeros(1), ["none"], path, "A network read by activity", "genes")
        return Result("not_enough_data", pd.DataFrame(), {}, path, checks,
                      f"{collection} is a weighted network: read it with method ulm on a genes run (de)")
    if de is not None:  # each target's own genes from a genes run, as published screens read them (move X1)
        return each_target(screen, design, out_dir, collection, method, de, targets, gene_lists, universe, backend,
                           seed)
    if targets:
        raise ValueError("G.meaning: targets are read from a genes run: give de (its ID) with targets")
    up, down, genes = (_genes_given(k, v) for k, v in (("up", up), ("down", down), ("genes", genes)))
    ranking = _ranking_given(ranking)
    lists = {k: v for k, v in (("up", up), ("down", down)) if v} or ({"genes": genes} if genes else {})
    no_sets = organism not in ("human", "mouse") and not gene_lists
    no_genes = not (lists if method == "ora" else ranking)
    if no_sets or no_genes:  # what is missing, alone (development run on GSE203592: no genes given, organism named)
        checks = figures.bars(np.zeros(1), ["none"], path, "No collection or no genes", "genes")
        missing = ([f"no collection: experiment.organism is {organism!r}, not human or mouse, and no gene_lists"]
                   if no_sets else []) + ([("no genes given: up, down or genes" if method == "ora" else
                                            "no ranking given") + " (from a gene test's run)"] if no_genes else [])
        return Result("not_enough_data", pd.DataFrame(), {"organism": organism}, path, checks,
                      "enrichment needs a collection and genes; " + "; ".join(missing))
    from . import charts
    given_universe = universe is not None
    universe = (set(map(str, universe)) if given_universe else
                set(screen.genes[screen.detected(np.ones(len(screen.label), bool), 0.01)]))
    background = (f"{len(universe)} genes given as the universe" if given_universe else
                  f"{len(universe)} genes detected in 1% of cells")
    if gene_lists:
        collection = "given"
        sets = {str(k): sorted(set(map(str, v)) & universe) for k, v in gene_lists.items()}
        sets = {k: v for k, v in sets.items() if v}
    else:
        sets = _sets(collection, organism, universe)
    sizes = {k: len(v) for k, v in sets.items()}
    from ..steps import enrichment
    if method == "ora":
        _enrich_checked(backend, out_dir)
        parts, n_in = [], {}
        for direction, gl in lists.items():
            n_in[direction] = len(set(gl) & universe)
            d = _enrich(gl, universe, sets, backend, out_dir)
            parts.append(d.drop(columns=["set_size"]).assign(direction=direction, list_size=n_in[direction]))
        df = pd.concat(parts, ignore_index=True)
        df["set_size"] = df["set"].map(sizes)
        df["gene_ratio"] = df["overlap"] / df["list_size"].clip(lower=1)
        df = df.sort_values(["q", "odds_ratio"], ascending=[True, False]).reset_index(drop=True)
        call = (packages.call("gseapy", "enrich", contrast="each list against the universe",
                              background=background, cutoff=1.0, directions=list(lists)) if backend == "gseapy" else
                packages.call("clusterProfiler", "enricher", contrast="each list against the universe",
                              background=f"{background}, those in some set kept (clusterProfiler's rule)",
                              minGSSize=10, maxGSSize=500, pAdjustMethod="BH", directions=list(lists)))
        df = df.drop(columns=["backend"])
        about = "among " + ", ".join(f"{n} {k} genes" for k, n in n_in.items())
        checks = charts.checks(charts.enrichment_dots(df.rename(columns={"direction": "group"}), n_in, path,
                                                      f"Gene sets {about} (prior knowledge)"), reference=None)
    else:
        rank = pd.Series({g: float(v) for g, v in ranking.items() if g in universe}).sort_values(ascending=False)
        df = enrichment.gsea(rank, sets, backend, out_dir, seed=seed).drop(columns=["set_size", "backend"])
        df["set_size"] = df["set"].map(sizes)
        df = df.sort_values(["q", "nes"], ascending=[True, False]).reset_index(drop=True)
        lo, hi = enrichment.SIZES[backend]
        call = (packages.call("gseapy", "prerank", contrast="the ranking's top against its bottom", min_size=lo,
                              max_size=hi, permutation_num=1000, seed=seed) if backend == "gseapy" else
                packages.call("clusterProfiler", "GSEA (fgsea)", contrast="the ranking's top against its bottom",
                              minGSSize=lo, maxGSSize=hi, pAdjustMethod="BH", seed=seed))
        about = f"on a ranking of {len(rank)} genes"
        checks = charts.checks(charts.pathway_dots(df.assign(group="GSEA"), path,
                                                   f"Gene sets {about} (prior knowledge)", score="nes"), zero=True)
    q = policy.get("q")
    n = int((df["q"] < q).sum())
    return Result("detected" if n else "not_detected", df,
                  {"collection": collection, "method": method, "universe": len(universe),
                   "tag": "prior knowledge", "package_calls": [call]}, path, checks,
                  f"{n} {collection} sets at adjusted p < {q} {about} ("
                  + (f"GSEApy {'enrich' if method == 'ora' else 'prerank'}" if backend == "gseapy" else
                     f"clusterProfiler {'enricher' if method == 'ora' else 'GSEA'}")
                  + f", against {background}; prior knowledge)")


def _genes_given(name, v):
    """A gene list as given: a list, or a table (its path, as an object's ID reaches a module) whose gene column is
    read; ValueError for anything else (a bare string was read as its letters)."""
    if v is None or isinstance(v, (list, tuple, set)):
        return None if v is None else [str(g) for g in v]
    if isinstance(v, str) and os.path.isfile(v):
        t = pd.read_csv(v, sep="\t")
        if "gene" in t.columns:
            return list(dict.fromkeys(t["gene"].astype(str)))
        raise ValueError(f"G.meaning's {name}: the table {os.path.basename(v)} has no gene column")
    raise ValueError(f"G.meaning's {name} is a list of genes, or a table with a gene column (an object's ID); for "
                     "each target's own changed genes, give de (a genes run) with method ora")


def _ranking_given(v):
    """A GSEA ranking as given: {gene: statistic}, or a table of one ranking (its path): gene, and stat, score or
    effect."""
    if v is None or isinstance(v, dict):
        return v
    if isinstance(v, str) and os.path.isfile(v):
        t = pd.read_csv(v, sep="\t")
        col = next((c for c in ("stat", "statistic", "score", "effect") if c in t.columns), None)
        if "gene" in t.columns and col and ("target" not in t.columns or t["target"].nunique() == 1):
            return dict(zip(t["gene"].astype(str), pd.to_numeric(t[col], errors="coerce")))
    raise ValueError("G.meaning's ranking is {gene: statistic} for every ranked gene, or a table of one ranking "
                     "(gene, and stat, score or effect); for each target's genes ranked from a genes run, give de "
                     "with method gsea")


def each_target(screen, design, out_dir, collection, method, de, targets, gene_lists, universe=None,
                backend="gseapy", seed=0):
    """G.meaning with a genes run (de) and method ora or gsea: each target's own genes, as published screens read a
    perturbation's changed genes: ORA of its changed genes, up and down apart, against the genes tested for it; or
    prerank GSEA of its genes by their signed test statistics. q: the package's BH within each list."""
    from .. import policy
    from ..steps import enrichment
    from .c import BIOLOGY_LINES, _target_by_column_heatmap
    path = os.path.join(out_dir, "figure.png")
    organism = value(design, "experiment.organism")
    table = pd.read_csv(de, sep="\t") if isinstance(de, str) else de.copy()
    missing = [c for c in ("target", "gene", "effect") if c not in table.columns]
    if missing:
        raise ValueError(f"G.meaning: the genes table lacks {missing}")
    if method == "ora" and not {"changed", "q"} & set(table.columns):
        raise ValueError("G.meaning: each target's changed genes need the genes table's changed or q column")
    table["target"], table["gene"] = table["target"].astype(str), table["gene"].astype(str)
    if targets:
        table = table[table["target"].isin(set(map(str, targets)))]
    if table.empty or (organism not in ("human", "mouse") and not gene_lists):
        checks = figures.bars(np.zeros(1), ["none"], path, "No targets or no collection", "genes")
        return Result("not_enough_data", pd.DataFrame(), {"organism": organism}, path, checks,
                      "each target's enrichment needs the targets in de and a collection (experiment.organism human "
                      "or mouse, or gene_lists)")
    allowed = set(map(str, universe)) if universe is not None else None
    tested_all = set(table["gene"]) & allowed if allowed is not None else set(table["gene"])
    if gene_lists:
        collection = "given"
        sets = {str(k): sorted(set(map(str, v)) & tested_all) for k, v in gene_lists.items()}
    else:
        sets = _sets(collection, organism, tested_all)
    q = policy.get("q")
    if method == "ora":
        _enrich_checked(backend, out_dir)
    parts = []
    for t, part in table.groupby("target", sort=False):
        part = part if allowed is None else part[part["gene"].isin(allowed)]
        tested = set(part["gene"])
        own = {k: [g for g in v if g in tested] for k, v in sets.items()}
        own = {k: v for k, v in own.items() if v}
        if method == "ora":
            hit = (part["changed"].astype(str).str.lower().isin(("true", "1")) if "changed" in part.columns
                   else pd.to_numeric(part["q"], errors="coerce") < q)
            eff = pd.to_numeric(part["effect"], errors="coerce")
            for direction, mask in (("up", hit & (eff > 0)), ("down", hit & (eff < 0))):
                genes = part.loc[mask, "gene"].tolist()
                if genes:
                    d = _enrich(genes, tested, own, backend, out_dir)
                    parts.append(d.drop(columns=["backend"]).assign(target=t, direction=direction,
                                                                     list_size=len(genes), tested=len(tested)))
        else:
            stat = pd.Series(enrichment.signed_statistic(part), index=part["gene"].to_numpy())
            d = enrichment.gsea(stat[np.isfinite(stat.to_numpy())], own, backend, out_dir, seed=seed)
            parts.append(d.drop(columns=["backend"]).assign(target=t, direction="ranked", tested=len(tested)))
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["target", "direction", "set", "q"])
    contrast = ("each target's changed genes, up and down apart, against the genes tested for it" if method == "ora"
                else "each target's genes ranked by their signed test statistics")
    call = packages.call("gseapy" if backend == "gseapy" else "clusterProfiler",
                         ("enrich" if method == "ora" else "prerank") if backend == "gseapy" else
                         ("enricher" if method == "ora" else "GSEA (fgsea)"), contrast=contrast)
    about = {"collection": collection, "method": method, "per_target": True, "tag": "prior knowledge",
             "targets": int(table["target"].nunique()), "package_calls": [call]}
    df["q"] = pd.to_numeric(df["q"], errors="coerce")
    hits = df[df["q"] < q]
    if hits.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, f"No {collection} set at q < {q} in any target", "sets")
        return Result("not_detected", df, about, path, checks,
                      f"no {collection} set at adjusted p < {q} in any of {about['targets']} targets ({contrast}; "
                      "prior knowledge)")
    tested_q = df.dropna(subset=["q"])
    if method == "ora":  # each target and set by its better direction: up positive, down negative
        best = tested_q.loc[tested_q.groupby(["target", "set"])["q"].idxmin()]
        per = best.assign(score=np.where(best["direction"] == "up", 1, -1) * -np.log10(best["q"].clip(lower=1e-300)))
        label = "-log10 q, up genes positive, down genes negative"
    else:
        per, label = tested_q.rename(columns={"nes": "score"}), "NES"
    checks = _target_by_column_heatmap(per.rename(columns={"set": "source"})[["target", "source", "score", "q"]],
                                       "source", "score", path, f"Gene sets in each target's genes ({collection})",
                                       label)
    lines = []
    for t, h in sorted(hits.groupby("target"), key=lambda x: -len(x[1]))[:BIOLOGY_LINES]:
        sides = ((("in its up genes", h[h["direction"] == "up"]), ("in its down genes", h[h["direction"] == "down"]))
                 if method == "ora" else (("at its top", h[h["nes"] > 0]), ("at its bottom", h[h["nes"] < 0])))
        ways = [f"{w} " + ", ".join(f"{r.set} (q {r.q:.1g})" for r in d.sort_values("q").head(3).itertuples())
                for w, d in sides if len(d)]
        lines.append(f"{t}: " + "; ".join(ways))
    return Result("detected", df, about, path, checks,
                  f"{len(hits)} target x set pairs at adjusted p < {q} in {hits['target'].nunique()} of "
                  f"{about['targets']} targets ({contrast}; "
                  + ("GSEApy" if backend == "gseapy" else "clusterProfiler") + f", {collection}; q within each list; "
                  "prior knowledge)", biology=lines)


def activities(screen, design, out_dir, collection, gene_lists, de, calibration, targets):
    """G.meaning's ulm: each target's activity of each source (a factor, a pathway, a set) from its tested genes'
    changes, beside the control-guide groups' activities from the same gene test (its calibration)."""
    from .. import blocks, policy
    from ..steps import enrichment
    from .c import BIOLOGY_LINES, _target_by_column_heatmap
    path = os.path.join(out_dir, "figure.png")
    organism = value(design, "experiment.organism")
    if de is None or (organism not in ("human", "mouse") and not gene_lists):
        checks = figures.bars(np.zeros(1), ["none"], path, "No genes run or no organism", "genes")
        return Result("not_enough_data", pd.DataFrame(), {"organism": organism}, path, checks,
                      "activity needs de, a genes run (C.genes), and experiment.organism, human or mouse")
    table = pd.read_csv(de, sep="\t") if isinstance(de, str) else de
    missing = [c for c in ("target", "gene", "effect") if c not in table.columns]
    if missing:
        raise ValueError(f"G.meaning: the genes table lacks {missing}")
    if targets:
        table = table[table["target"].astype(str).isin(set(map(str, targets)))]
    net = enrichment.network(collection, organism, gene_lists)
    kind = "set" if gene_lists else {"collectri": "factor", "progeny": "pathway"}.get(collection, "set")
    source = "given lists" if gene_lists else {"collectri": "CollecTRI", "progeny": "PROGENy"}.get(collection,
                                                                                                   collection)
    per = enrichment.activities(table, net)
    groups = ([calibration] if isinstance(calibration, pd.DataFrame) else
              [pd.read_csv(calibration, sep="\t")] if isinstance(calibration, str) else
              blocks.genes_calibration_tables(de) if isinstance(de, str) else [])
    band = enrichment.activity_band(pd.concat(groups, ignore_index=True), net) if groups else {}
    q = policy.get("q")
    per["band"] = per["source"].map(band)
    per["past_band"] = per["activity"].abs() > per["band"]  # no line: never past it
    hits = per[(per["q"] < q) & (per["past_band"] if band else True)]
    call = packages.call("decoupler", "mt.ulm", contrast="each target's signed test statistics over its own tested "
                         "genes", net=source, tmin=5)
    n_groups = sum(int(g["target"].nunique()) for g in groups)
    line = (f"past the control-guide groups' line ({n_groups} groups)" if band else
            "with no control-guide line (the genes run has no calibration)")
    about = {"collection": collection, "method": "ulm", "tag": "prior knowledge",
             "sources": int(per["source"].nunique()), "targets": int(per["target"].nunique()),
             "package_calls": [call],
             "control_guide_line": "95th percentile of the groups' absolute activities" if band else "not drawn"}
    if per.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, f"No {kind} with 5 genes among the tested genes", "genes")
        return Result("not_enough_data", per, about, path, checks,
                      f"no {kind} of {source} has 5 of its genes among a target's tested genes")
    checks = _target_by_column_heatmap(per, "source", "activity", path,
                                       f"{kind.capitalize()} activity from each target's changes ({source})",
                                       "Activity (ULM t)")
    lines = []  # each target past the line, the most sources first: its four highest and four lowest
    for t, h in sorted(hits.groupby("target"), key=lambda x: -len(x[1]))[:BIOLOGY_LINES]:
        up = h[h["activity"] > 0].sort_values("activity", ascending=False).head(4)
        down = h[h["activity"] < 0].sort_values("activity").head(4)
        ways = [f"{w} " + ", ".join(f"{r.source} ({r.activity:+.1f})" for r in d.itertuples())
                for w, d in (("higher", up), ("lower", down)) if len(d)]
        lines.append(f"{t}: " + "; ".join(ways))
    return Result("detected" if len(hits) else "not_detected", per.reset_index(drop=True), about, path, checks,
                  f"{len(hits)} target x {kind} pairs at q < {q} {line} (decoupler's ULM on each target's signed test "
                  f"statistics over its tested genes, {source}; q: decoupler's BH over the {kind}s within each target; "
                  "prior knowledge)", biology=lines)


# ---- direct targets ------------------------------------------------------------------------------------------------
@register("G.direct_targets", Contract(
    serves="G.direct_targets",
    applies_when="a perturbed regulator (a transcription factor) whose changed genes were measured",
    needs="a genes run's table (target, gene, effect, q; C.genes), the organism (human or mouse), CollecTRI's "
          "regulons (kept offline, gene_sets.collectri) or a regulon table given",
    returns="per target whose target gene is a transcription factor in the regulons: its activity from its known "
            "targets' changes (decoupler's univariate linear model on the target's signed test statistics over the "
            "genes tested for it; decoupler and CollecTRI are Pertura's route, which no reviewed screen uses), the "
            "activity's p (activity_p, decoupler's own BH over all factors within the target), its q adjusted again "
            "over the factors read, and its rank among all factors; the "
            "share of its changed genes that are its known targets, against the genes tested for it (Fisher's exact "
            "test), and how many of them move the way the regulon's sign and the perturbation's direction predict; "
            "prior knowledge, tagged so",
    fails_when=[("a regulon is curated from other cells and conditions; a factor can act here through other genes",
                 "its activity and its targets' share are read as support for a direct effect, never as proof"),
                ("an activity falls only where the factor's regulon is in use in these cells",
                 "the factor's own mRNA beside its activity (B.efficacy)")],
    tested_against="synthetic: a CRISPRi target whose planted regulon falls has the lowest activity of all factors "
                   "and its targets enriched among its changed genes; a target with a random regulon has neither",
    choices={"tmin": [5, 10]},
    inputs={"de": "a C.genes run, by its ID or <run>:changed_genes (the run's own test of every gene is read); or a "
                  "genes table (target, gene, effect, q, one row per target and gene), or its TSV path",
            "targets": "the targets to read (default: every target whose target gene is a factor in the regulons)",
            "net": "a regulon table (source, target, weight) in place of CollecTRI, or its TSV path"}))
def direct_targets(screen, design, out_dir, de=None, targets=None, net=None, tmin=5):
    import decoupler as dc
    from scipy import stats
    from .. import gene_sets
    from . import charts
    from ..steps.enrichment import one_pair_each, signed_statistic
    from .b import expected_sign
    path = os.path.join(out_dir, "figure.png")
    organism = value(design, "experiment.organism")
    if de is None:
        raise ValueError("G.direct_targets needs de, a genes run's table (target, gene, effect, q)")
    de = pd.read_csv(de, sep="\t") if isinstance(de, str) else de
    source = "CollecTRI" if net is None else "given"
    if net is None:
        if organism not in ("human", "mouse"):
            checks = figures.bars(np.zeros(1), ["none"], path, "No regulons for this organism", "factors")
            return Result("not_enough_data", pd.DataFrame(), {"organism": organism}, path, checks,
                          "CollecTRI's regulons need experiment.organism, human or mouse")
        net = gene_sets.collectri(organism)
    net = pd.read_csv(net, sep="\t") if isinstance(net, str) else net
    missing = [c for c in ("target", "gene", "effect", "q") if c not in de.columns]
    if missing:
        raise ValueError(f"G.direct_targets: the genes table lacks {missing}")
    de = de[np.isfinite(pd.to_numeric(de["effect"], errors="coerce"))].copy()
    de["target"], de["gene"] = de["target"].astype(str), de["gene"].astype(str)
    repeated = de[de.duplicated(["target", "gene"], keep=False)]
    if len(repeated):
        raise ValueError(f"G.direct_targets: {len(repeated)} rows repeat a (target, gene) pair, such as "
                         f"{repeated[['target', 'gene']].head(3).values.tolist()}; give one row per pair")
    from statsmodels.stats.multitest import multipletests
    regulons = net[["source", "target", "weight"]]
    one_pair_each(regulons)
    rows = []
    for t in (list(dict.fromkeys(de["target"])) if targets is None else list(targets)):
        own = screen.own_gene_of(t)
        d = de[de["target"] == str(t)]
        if own is None or d.empty:
            continue
        # each target's activity from its own tested genes only: a gene not tested for it is not a zero change
        tested = set(d["gene"])
        stat = pd.Series(signed_statistic(d), index=d["gene"].to_numpy())  # not the raw fold change of sparse genes
        es, pv = dc.mt.ulm(data=pd.DataFrame([stat[np.isfinite(stat.to_numpy())]], index=[t]), net=regulons,
                           tmin=tmin)
        if own not in es.columns or t not in es.index:
            continue
        sign = expected_sign(design, screen.label_guide(t))
        reg = net[(net["source"] == own) & net["target"].isin(tested)]
        hit = set(d.loc[d["q"] < 0.05, "gene"])
        known = set(reg["target"])
        a, b = len(hit & known), len(hit - known)
        c, e = len(known - hit), len(tested - hit - known)  # the background: this target's tested genes
        odds, p_fisher = stats.fisher_exact([[a, b], [c, e]], alternative="greater") if (a + b) else (np.nan, 1.0)
        w = dict(zip(reg["target"], np.sign(reg["weight"])))
        eff = d.set_index("gene")["effect"]
        agree = sum(1 for g in hit & known if sign and np.sign(eff[g]) == sign * w[g])
        act = es.loc[t]
        rows.append({"target": t, "factor": own, "expected": int(sign), "activity": float(act[own]),
                     "activity_p": float(pv.loc[t, own]),
                     # rank 1: the factor whose activity moves most the way the perturbation should move it
                     "activity_rank": int((act.rank(ascending=sign < 0) if sign else act.abs().rank(ascending=False))
                                          [own]),
                     "factors": int(act.notna().sum()), "genes_tested": len(tested),
                     "known_targets_tested": len(known), "changed": len(hit),
                     "changed_known_targets": a, "odds_ratio": float(odds), "p_fisher": float(p_fisher),
                     "moving_as_predicted": int(agree)})
    df = pd.DataFrame(rows)
    if df.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target is a factor in the regulons", "factors")
        return Result("not_enough_data", df, {"tag": "prior knowledge"}, path, checks,
                      "no target perturbs a transcription factor with targets among the tested genes")
    # the factors read form one family: their activities' p-values adjusted together
    df["activity_q"] = multipletests(df["activity_p"], method="fdr_bh")[1]
    df["fisher_q"] = multipletests(df["p_fisher"], method="fdr_bh")[1]
    call = packages.call("decoupler", "mt.ulm", contrast="each target's signed test statistics over its own tested "
                         "genes", net=source, tmin=tmin)
    checks = charts.checks(charts.ranked_targets(df.rename(columns={"activity_q": "q"}), "activity", path,
                                                 "Activity of the perturbed factor, from its known targets",
                                                 "Factor's activity (ULM), from its targets", q="q"), zero=True)
    n = int(((df["activity_q"] < 0.05) & (np.sign(df["activity"]) == df["expected"])).sum())
    changed_only = bool((de["q"] < 0.05).all())
    note = (" The genes table holds only changed genes, so each target's tested genes, the Fisher test's background, "
            "are not known; give the run's full genes table." if changed_only else "")
    return Result("detected" if n else "not_detected", df,
                  {"tag": "prior knowledge", "factors_read": int(len(df)), "package_calls": [call],
                   "changed_genes_only": changed_only}, path, checks,
                  f"{n} of {len(df)} perturbed factors change their known targets' activity the expected way at "
                  "q < 0.05 over the factors read (decoupler's ULM on each target's tested genes, CollecTRI; prior "
                  "knowledge)." + note)
