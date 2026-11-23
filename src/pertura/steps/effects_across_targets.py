"""Steps on a table of effects across targets (a pt.scores or C.programs result: target, score, effect), the P1
paradigm's middle layers (docs/adr/0031, phase 2; docs/build/biology_depth_papers/paradigms_of_deep_perturbseq_
analysis.md):
- correlate: for every two programs, the correlation of the targets' effects on the one with their effects on the
  other (Pearson or Spearman), each mode apart where the screen has two, Benjamini-Hochberg over every pair
  (E.program_axis's);
- rank_on: the targets ranked on two programs at once, one up and the other down (the axis score: the effect on the
  first minus the effect on the second), read against the control-guide groups' axis scores where a calibration of
  the same scores is given;
- clusters: targets grouped by their effects (or their similarity): hierarchical with average linkage on the
  correlation distance and the cut of 2 to 10 sets with the best silhouette (E.co_functional_sets'), scikit-learn's
  HDBSCAN (min_cluster_size 4, as Replogle et al. 2022), or Leiden on the targets' neighbour graph; and the sets'
  stability over resampled features (Zhu et al. 2026's consensus).
"""
import itertools

import numpy as np
import pandas as pd

MIN_TARGETS = 5
CLUSTER_METHODS = ("hierarchical", "hdbscan", "leiden")


def one_row_per_key(table, keys, what):
    """The table, refused when two of its rows share a key: which of them to take is not the table's to say (an
    extra column, such as a condition or a unit, splits it first)."""
    keys = list(keys)
    repeated = table[table.duplicated(keys, keep=False)]
    if len(repeated):
        raise ValueError(f"{what}: {len(repeated)} rows repeat a ({', '.join(keys)}) key, such as "
                         f"{repeated[keys].head(3).values.tolist()}; give one row per key (split the table by its "
                         f"other columns, {[c for c in table.columns if c not in keys][:6]}, first)")
    return table


def effects_matrix(table, value="effect"):
    """Targets by scores (programs or sets) of the value; a target not measured on a score is missing there, not 0.
    A table with two rows for one target and score is refused."""
    one_row_per_key(table, ("target", "score"), "the effects table")
    return table.pivot(index="target", columns="score", values=value).dropna(how="all").dropna(axis=1, how="all")


def complete_rows(M):
    """The targets measured on every feature, and those left out, in their order: a missing effect is not 0."""
    gone = sorted(M.index[M.isna().any(axis=1)], key=str)
    return M.drop(index=gone), gone


def complete_pairs(R):
    """A similarity matrix cut to targets whose every pair was observed: the target with the most unobserved pairs
    left out first (a tie by name) until none is; (the matrix, those left out). An unobserved pair is not r = 0."""
    gone = []
    while len(R) and R.isna().to_numpy().any():
        miss = R.isna().sum(axis=1)
        worst = sorted(miss[miss == miss.max()].index, key=str)[0]
        gone.append(worst)
        R = R.drop(index=worst, columns=worst)
    return R, gone


def correlate(table, modes=None, method="pearson", top_genes=None, min_targets=MIN_TARGETS):
    """Every two programs' correlation over the targets, per mode. Returns the table: guide_mode, program_a, program_b, r,
    p, targets, shared_top_genes, q."""
    from scipy import stats
    from statsmodels.stats.multitest import multipletests
    if method not in ("pearson", "spearman"):
        raise ValueError(f"method is pearson or spearman, not {method!r}")
    M = effects_matrix(table)
    modes = modes or {t: "all" for t in M.index}
    rows = []
    for mode in sorted(set(modes.get(t, "all") for t in M.index)):
        sub = M.loc[[t for t in M.index if modes.get(t, "all") == mode]]
        for a, b in itertools.combinations(sub.columns, 2):
            x = sub[[a, b]].dropna()
            if len(x) < min_targets:
                continue
            r, p = (stats.pearsonr if method == "pearson" else stats.spearmanr)(x[a], x[b])
            shared = (len(set(top_genes.get(a, [])) & set(top_genes.get(b, []))) if top_genes else None)
            rows.append({"guide_mode": mode, "program_a": a, "program_b": b, "r": float(r), "p": float(p),
                         "targets": int(len(x)), "shared_top_genes": shared})
    df = pd.DataFrame(rows, columns=["guide_mode", "program_a", "program_b", "r", "p", "targets", "shared_top_genes"])
    df["q"] = multipletests(df["p"], method="fdr_bh")[1] if len(df) else []
    return df


def correlate_against_groups(table, groups, modes=None, method="pearson", top_genes=None, min_targets=MIN_TARGETS):
    """correlate over the targets, with the same correlation over the control-guide groups beside each pair
    (groups: the same scores' calibration, pt.calibrate on them), as a reference read beside r: programs whose effects
    correlate over the groups as they do over the targets may correlate by construction (shared genes, the cells'
    common variation). Adds r_groups and the groups counted. A reference, not a test: r_groups rests on a handful of
    groups, so no verdict is drawn from the two numbers."""
    out = correlate(table, modes, method, top_genes, min_targets)
    null = correlate(groups, None, method, None, min_targets=3)
    by = {(a, b): (r, n) for a, b, r, n in zip(null["program_a"], null["program_b"], null["r"], null["targets"])}
    got = [by.get((a, b)) or by.get((b, a)) for a, b in zip(out["program_a"], out["program_b"])]
    out["r_groups"] = [g[0] if g else np.nan for g in got]
    out["groups"] = [g[1] if g else 0 for g in got]
    return out


def _together(table, by, names, method, seed, rng=None):
    """{(a, b): [together in each leave-one-out]} for the pairs of names both clustered in it; with rng, each unit's
    targets shuffled first, so what a set shares across units is lost (the reference)."""
    out = {}
    if rng is not None:
        parts = []
        for _, part in table.groupby(by):
            ts = sorted(set(part["target"].astype(str)))
            swap = dict(zip(ts, rng.permutation(ts)))
            parts.append(part.assign(target=part["target"].astype(str).map(swap)))
        table = pd.concat(parts, ignore_index=True)
    for u in sorted(set(table[by].astype(str))):
        rest = table[table[by].astype(str) != u]
        M = rest.groupby(["target", "score"], as_index=False)["effect"].mean()
        M, _ = complete_rows(effects_matrix(M))
        M = M.reindex([n for n in names if n in M.index])
        if len(M) < 4:
            continue
        got, _ = clusters(M.to_numpy(), list(M.index), method, seed=seed)
        if got is None:
            continue
        for a, b in itertools.combinations(names, 2):
            if a in got and b in got:  # a target left out of a draw counts for neither
                out.setdefault((a, b), []).append(got[a] == got[b] and got[a] != -1)
    return out


def kept_without_one(table, by, labels, method="hierarchical", seed=0, draws=20):
    """Each set's members kept together with one unit left out, beside the same share when each unit's targets are
    shuffled first (by chance: what a set shares across units is lost). For each unit in turn, the targets' effects
    averaged over the others are clustered again; per set, the share of its members' pairs together again. A set
    HDBSCAN leaves unassigned (-1) has none. Returns {set: {kept, by_chance}}: a description with its reference, not a
    test (with few units each draw reuses most of the data)."""
    names = list(labels)
    seen = _together(table, by, names, method, seed)
    rng = np.random.default_rng(seed)
    chance = [_together(table, by, names, method, seed, rng) for _ in range(draws)]
    out = {}
    for k in sorted(set(labels.values()) - {-1}):
        members = [n for n in names if labels[n] == k]
        pairs = [p for p in itertools.combinations(members, 2) if p in seen]
        kept = [x for p in pairs for x in seen[p]]
        by_chance = [x for c in chance for p in pairs for x in c.get(p, [])]
        out[k] = {"kept": round(float(np.mean(kept)), 3) if kept else None,
                  "by_chance": round(float(np.mean(by_chance)), 3) if by_chance else None}
    return out


def rank_on(table, up, down, null=None):
    """The targets ranked on two programs at once: axis = effect on `up` minus effect on `down`. null: the same
    scores' table for control-guide groups (pt.calibrate on the scores), whose axis values are the reference.
    Returns (per target: effect_up, effect_down, axis, axis_rank, beyond_groups; the groups' summary)."""
    M = effects_matrix(table)
    for p in (up, down):
        if p not in M.columns:
            raise ValueError(f"no score {p!r} in the table; its scores are {sorted(M.columns)[:20]}")
    per = pd.DataFrame({"target": M.index, "effect_up": M[up].to_numpy(), "effect_down": M[down].to_numpy()})
    per["axis"] = per["effect_up"] - per["effect_down"]
    per = per.dropna(subset=["axis"]).sort_values("axis", ascending=False).reset_index(drop=True)
    per["axis_rank"] = np.arange(1, len(per) + 1)  # not "rank", a DataFrame method (item 18.2)
    summary = {"up": up, "down": down, "groups": 0}
    if null is not None and len(null):
        G = effects_matrix(null)
        if up in G.columns and down in G.columns:
            g = (G[up] - G[down]).dropna().to_numpy(dtype=float)
            if len(g):
                p95 = float(np.percentile(g, 95))
                per["beyond_groups"] = per["axis"] > p95
                summary.update(groups=int(len(g)), group_axis=[round(float(x), 6) for x in g],
                               groups_95th_percentile=p95, targets_beyond=per.loc[per["beyond_groups"], "target"]
                               .astype(str).tolist())
    return per, summary


def clusters(X, names, method="hierarchical", min_cluster_size=4, n_neighbors=5, resolution=1.0, seed=0,
             distance=None):
    """Sets of targets from their rows X (targets by features), by the method. distance: a precomputed distance
    (1 - r of a similarity), else the correlation distance of the rows. Returns (labels per name, silhouette)."""
    if method not in CLUSTER_METHODS:
        raise ValueError(f"method is one of {list(CLUSTER_METHODS)}, not {method!r}")
    X = np.asarray(X, dtype=float)
    if distance is None:
        with np.errstate(invalid="ignore", divide="ignore"):
            D = np.clip(1 - np.corrcoef(X), 0, 2)
    else:
        D = np.clip(np.asarray(distance, dtype=float), 0, 2)
    D = np.nan_to_num(D, nan=1.0)
    np.fill_diagonal(D, 0.0)
    if method == "hierarchical":  # on the distances above, where a flat row's undefined correlation reads as 1
        labels, best = _silhouette_cut_precomputed(D)
    elif method == "hdbscan":
        from sklearn.cluster import HDBSCAN
        # on a copy: scikit-learn's HDBSCAN turns a precomputed matrix into its reachability distances in place
        labels = HDBSCAN(min_cluster_size=min_cluster_size, metric="precomputed").fit_predict(D.copy())
        best = None
    else:
        import igraph as ig
        import leidenalg
        k = min(n_neighbors, len(X) - 1)
        nearest = np.argsort(D, axis=1)[:, 1:k + 1]
        edges = {tuple(sorted((i, int(j)))) for i in range(len(X)) for j in nearest[i]}
        g = ig.Graph(n=len(X), edges=sorted(edges))
        part = leidenalg.find_partition(g, leidenalg.RBConfigurationVertexPartition,
                                        resolution_parameter=resolution, seed=seed)
        labels = np.asarray(part.membership)
        best = None
    if labels is None:
        return None, None
    if best is None and len(set(labels) - {-1}) >= 2:
        from sklearn.metrics import silhouette_score
        keep = np.asarray(labels) != -1
        if len(set(np.asarray(labels)[keep])) >= 2 and keep.sum() > 2:
            best = float(silhouette_score(D[np.ix_(keep, keep)], np.asarray(labels)[keep], metric="precomputed"))
    return dict(zip(names, (int(x) for x in labels))), (float(best) if best is not None else None)


def _silhouette_cut_precomputed(D, k_max=10):
    """Average-linkage clusters on a precomputed distance, cut at the k (2 to k_max) with the best silhouette."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    from sklearn.metrics import silhouette_score
    Z = linkage(squareform(D, checks=False), "average")
    best, labels = None, None
    for k in range(2, min(k_max, len(D) - 1) + 1):
        lab = fcluster(Z, k, criterion="maxclust")
        if len(set(lab)) < 2:
            continue
        s = silhouette_score(D, lab, metric="precomputed")
        if best is None or s > best:
            best, labels = s, lab
    return labels, best


def stability(X, names, labels, method="hierarchical", draws=50, seed=0, **kw):
    """Each set's stability: over draws of the features resampled with replacement, the share of its members' pairs
    that fall in one set again (Zhu et al. 2026's consensus over subsamples). Returns {set: share}."""
    X = np.asarray(X, dtype=float)
    rng = np.random.default_rng(seed)
    together = np.zeros((len(names), len(names)))
    runs = 0
    for _ in range(draws):
        cols = rng.integers(0, X.shape[1], X.shape[1])
        try:
            got, _ = clusters(X[:, cols], names, method, seed=seed, **kw)
        except ValueError:  # a draw too degenerate to cut
            continue
        if got is None:
            continue
        lab = np.array([got[n] for n in names])
        together += (lab[:, None] == lab[None, :]) & (lab[:, None] != -1)
        runs += 1
    out = {}
    for k in sorted(set(labels.values())):
        idx = [i for i, n in enumerate(names) if labels[n] == k]
        if len(idx) < 2 or not runs:
            out[k] = None
            continue
        pairs = [(i, j) for i, j in itertools.combinations(idx, 2)]
        out[k] = float(np.mean([together[i, j] / runs for i, j in pairs]))
    return out
