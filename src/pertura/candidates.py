"""The first look's candidate structures (docs/adr/0039, D3): facts read from the first look's tables, beside which
the agent writes what it sees and chooses its main line. Each list uses its module's own measure. Code lists; it
never chooses, ranks one line above another, or names a hit.

    sets_alike(pairs)                  # E.similar_targets' result: targets joined by pairs beyond the control pairs
    changing_between_conditions(per)   # E.condition_x_perturbation's result: target-by-score changes at q < Q
    far_from_controls(targets)         # A.screen_overview's targets: past their effect line, farthest first
    states_shifting(per)               # C.states_and_composition's result: target-by-state share changes at q < Q
    controls_differ(result)            # D.cutting_response's result: cutting against non-targeting control cells
    known_complexes(targets, sets)     # beside a set alike: the GO cellular components holding two or more
"""
import pandas as pd

# the first look's sections, in the order the report and the page give them (survey first, then depth): each one's key,
# its label, and the candidate structure that lists it (None: the states map, read whenever the states ran)
SECTIONS = {"states_map": ("The cells' states", None),
            "far_from_controls": ("Targets far from the control cells", "targets_far_from_the_control_cells"),
            "sets_alike": ("Sets of targets alike", "sets_of_targets_alike"),
            "state_shares": ("Targets shifting the states' shares", "targets_shifting_state_shares"),
            "condition_differences": ("Effects that differ between conditions", "targets_changing_between_conditions"),
            "control_kinds": ("The two kinds of control", "the_two_kinds_of_control")}
# the kinds of conclusion a design can reach, beside the first look's maps (docs/adr/0043, part A; 2026-11-07, P17):
# what each reads, and how a worker reads it across the screen. The lines of Pertura's runs started from the largest
# effect, and 62% of the points lost on eight screens were never looked at: states read by their biology, what a
# target's genes are, the conditions apart. Which kinds apply follows the design's facts (reach_of_the_design)
# the survey's comparisons that read each kind (pertura.survey's rows; the screen overview's by its id): where the
# survey ran one, its run is read before a worker runs it again
KIND_ROWS = {"states": ("states", "shifts_per_condition"), "conditions_apart": ("effect_changing",),
             "second_readout": ("proteins",), "targets_on_each_other": ("distance:overview",)}
KINDS = {
    "states": ("the cells' states, named from their markers, and each target's shares of them",
               "`states`: name each state from its markers and its naming evidence; its biology lines give each "
               "target's shifts"),
    "programmes": ("what each target's genes are: programmes, pathways and regulators",
                   "`scores` (gene_sets hallmark), and `enrichment` and its view `direct_targets` on the `genes` run "
                   "(de); their biology lines give each target's programmes"),
    "conditions_apart": ("each target within each condition, and how its effect changes between them",
                         "`genes` and `states` on each condition's cells (`by` the condition's column, or `cells` "
                         "{column: value}), and `conditions` for each pair of conditions (pair)"),
    "targets_on_each_other": ("each target's change of the other targets' own genes",
                              "`genes` with genes \"targets_own\", the network (the overview's last panel and its "
                              "biology lines show it too); follow each target that changes several others"),
    "second_readout": ("each target's proteins against the control cells, beside its RNA",
                       "`scores` with gene_sets \"proteins\": each target's antibody counts (CLR) against the control "
                       "cells, with the same test and the control-guide groups"),
}
# the routes around each kind of block (record.BLOCK_KINDS), as the follow-up moves card's moves published screens
# made (its ids; follow_up_moves.md, "When a line stops")
BLOCK_ROUTES = {
    "the readout does not measure it": "X2 (the downstream genes or the network: `enrichment` view "
                                       "`direct_targets`), C4 (dose, by guide strength), T3 (whether each "
                                       "perturbation acts), a second readout where the screen holds one",
    "two things cannot be told apart": "L1 (between or within states: `states` view `within`), O1 (a score of a "
                                       "programme in place of clusters), L3 (along a trajectory: `states` with "
                                       "trajectory_root)",
    "pooled": "C1 (each condition apart, then condition by perturbation)",
    "too few": "T6 (guides pooled, a coarser state, guide by guide), or no route: the design holds what it holds",
    "confounded by the design": "T7 (within one batch, or the batch as a covariate); often no route, with the reason",
    "data quality": "T2 (the test calibrated), matched depth, a minimum expression",
    "a reference missing": "another reference: the part that stands out from the average perturbation (`against` "
                           "other_targets); or no route",
    "not a block": "a line set aside for its interest, not its data: none needed"}
# the sections drawn by another of their run's figures than its main one: the states' shares by target, where the
# states run's main figure is the map of the states (both sections showed the same map)
SECTION_FIGURES = {"state_shares": "figure_composition"}
BEYOND = 0.05  # E.similar_targets: a pair correlates more than 95% of the control-guide pairs
Q = 0.05  # E.condition_x_perturbation's level
SHOWN = 8  # the entries each list gives; the count says how many there were
PLACES = 2  # the targets shown for each state and each way its share moves (states_shifting)
SHOWN_BY_STATE = 24  # the targets those places show at most


def _number(s):
    return pd.to_numeric(s, errors="coerce")


def sets_alike(pairs, reference=None, beyond=BEYOND, low=None):
    """The sets of targets alike, each with its pairs' correlations and how many of them pass the control pairs
    (control_pairs_as_high at or under beyond): {sets: [...], count, how}, the largest sets first. With the control
    pairs' 5th percentile (low), each set also names the targets opposite it: those whose correlation with its members
    averages below low, the most opposite first (groups of perturbations with opposite effects).

    With the control-guide pairs' 95th percentile (reference, the module's own), the sets are the merges of the
    targets' average-linkage tree (E.similar_targets' own order) whose members correlate above it on average, nested
    sets included, the most alike first: a tight core and the wider set it joins both show. Without it, the targets
    joined by pairs that pass, which can chain unlike targets together through one target."""
    if pairs is None or pairs.empty or not {"target", "other", "correlation", "control_pairs_as_high"} <= set(pairs):
        return {"sets": [], "count": 0}
    passing = _number(pairs["control_pairs_as_high"]) <= beyond
    if reference is not None:
        groups, how = _merges(pairs, float(reference)), f"average-linkage merges above the control pairs' {reference:.2f}"
    else:
        groups, how = _joined(pairs[passing]), "joined by pairs beyond the control pairs"
    names, r = _matrix(pairs)
    at = {n: i for i, n in enumerate(names)}
    sets = []
    for members in groups:
        inside = pairs[pairs["target"].isin(members) & pairs["other"].isin(members)]
        s = {"targets": sorted(members), "mean_correlation": round(float(_number(inside["correlation"]).mean()), 2),
             "pairs": int(len(inside)), "pairs_beyond_the_control_pairs": int(passing[inside.index].sum())}
        if low is not None:
            mean = r[:, [at[m] for m in members]].mean(1)
            s["opposite"] = sorted(({"target": n, "mean_correlation": round(float(mean[i]), 2)}
                                    for i, n in enumerate(names) if n not in members and mean[i] < float(low)),
                                   key=lambda x: x["mean_correlation"])[:SHOWN]
        sets.append(s)
    sets.sort(key=lambda s: (-s["mean_correlation"], -len(s["targets"]), s["targets"]))
    if low is not None:
        how += f"; opposite a set: the other targets whose correlation with its members averages below {low:.2f}"
    return {"sets": sets[:SHOWN], "count": len(sets), "how": how}


def _joined(ok):
    parent = {}

    def root(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in zip(ok["target"], ok["other"]):
        parent[root(a)] = root(b)
    groups = {}
    for t in list(parent):
        groups.setdefault(root(t), set()).add(t)
    return [g for g in groups.values() if len(g) > 1]


def _matrix(pairs):
    """The targets of the pairs and their correlations as a matrix; a pair the table lacks counts as uncorrelated."""
    import numpy as np
    names = sorted(set(pairs["target"]) | set(pairs["other"]))
    at = {n: i for i, n in enumerate(names)}
    r = np.zeros((len(names), len(names)))
    for a, b, c in pairs[["target", "other", "correlation"]].itertuples(index=False):
        if pd.notna(c):
            r[at[a], at[b]] = r[at[b], at[a]] = float(c)
    return names, r


def apart(sets):
    """The sets alike (sets_alike's) that no other of them holds: a tight core is shown once, as the widest set it
    joins among those listed, so each set names a block of the screen apart from the others."""
    return [s for s in sets if not any(set(s["targets"]) < set(o["targets"]) for o in sets)]


def leaf_order(pairs):
    """The targets in the leaf order of the average-linkage tree the sets alike come from, where each set is a run of
    neighbours."""
    from scipy.cluster.hierarchy import leaves_list
    names, tree = _tree(pairs)
    return names if tree is None else [names[i] for i in leaves_list(tree)]


def _tree(pairs):
    """The targets of the pairs and their average-linkage tree on 1 - correlation (None for fewer than two)."""
    import numpy as np
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform
    names, r = _matrix(pairs)
    if len(names) < 2:
        return names, None
    d = np.clip(1.0 - r, 0.0, 2.0)
    np.fill_diagonal(d, 0.0)
    return names, linkage(squareform(d, checks=False), "average")


def _merges(pairs, reference):
    """The merges of the average-linkage tree on 1 - correlation made below 1 - reference: each merge's members."""
    names, tree = _tree(pairs)
    if tree is None:
        return []
    members = {i: {n} for i, n in enumerate(names)}
    out = []
    for k, (a, b, height, _) in enumerate(tree):
        members[len(names) + k] = members[int(a)] | members[int(b)]
        if height < 1.0 - reference:  # the average correlation between the two sides is above the reference
            out.append(members[len(names) + k])
    return out


def changing_between_conditions(per, q=Q):
    """The targets whose effect on a score changes between the two conditions at q < q, each with those scores and the
    change (effect in the second condition minus the first): {targets: [...], count}, the most scores first."""
    if per is None or per.empty or not {"target", "score", "interaction", "q"} <= set(per):
        return {"targets": [], "count": 0}
    hits = per[_number(per["q"]) < q]
    out = [{"target": t, "changes": {s: round(float(v), 3) for s, v in zip(rows["score"], rows["interaction"])}}
           for t, rows in hits.groupby("target")]
    out.sort(key=lambda x: (-len(x["changes"]), x["target"]))
    return {"targets": out[:SHOWN], "count": len(out)}


def far_from_controls(targets):
    """The targets past their effect line (each one's energy distance against control-guide groups of its own number
    of cells), farthest first, with the genes they change where C.genes ran: {targets: [...], count}."""
    if targets is None or targets.empty or "target" not in targets:
        return {"targets": [], "count": 0}
    dist = "e_distance" if "e_distance" in targets else ("distance" if "distance" in targets else None)
    if dist is None:
        return {"targets": [], "count": 0}
    t = targets.assign(_d=_number(targets[dist]))
    if "past_the_line" in t:
        t = t[t["past_the_line"].astype(str).str.lower().isin(("true", "1", "yes"))]
    t = t.dropna(subset=["_d"]).sort_values("_d", ascending=False)
    genes = _number(t["genes_changed"]) if "genes_changed" in t else pd.Series(index=t.index, dtype=float)
    out = [{"target": r["target"], "distance": round(float(r["_d"]), 3),
            **({"genes_changed": int(genes[i])} if pd.notna(genes.get(i)) else {})} for i, r in t.iterrows()]
    return {"targets": out[:SHOWN], "count": len(out)}


def states_shifting(per, q=Q, places=PLACES, most=SHOWN_BY_STATE):
    """The targets whose share of a state differs from their control cells' at q < q (C.states_and_composition's
    result), each with those states, its share and the controls': {targets: [...], count}. Shown: for each state the
    `places` targets that raise its share most and the `places` that lower it most, place by place across the states;
    every state's first in each way, and the later places up to `most` targets in all (docs/adr/0045, D5; GSE342764 on
    0adebaa: 37 targets, 8 shown by how many states each moved, and Zmynd8, which lowered one, was not among them; a
    cut of state-by-state places left 6 of 13 states none). Without the shares, the most states first."""
    if per is None or per.empty or not {"target", "state", "q"} <= set(per):
        return {"targets": [], "count": 0}
    hits = per[_number(per["q"]) < q]
    name = "state_name" if "state_name" in hits else "state"
    entry = {t: {"target": t, "states": {str(r[name]): {"share": round(float(r.get("share", float("nan"))), 3),
                                                        "control_share": round(float(r.get("control_share",
                                                                                           float("nan"))), 3)}
                                         for _, r in rows.iterrows()}}
             for t, rows in hits.groupby("target")}
    if not {"share", "control_share"} <= set(hits):
        out = sorted(entry.values(), key=lambda x: (-len(x["states"]), x["target"]))
        return {"targets": out[:SHOWN], "count": len(out)}
    shift = hits.assign(_shift=_number(hits["share"]) - _number(hits["control_share"]))
    ways = []  # each state's targets raising its share and lowering it, the largest first
    for _, rows in shift.groupby(shift[name].astype(str), sort=True):
        ways += [list(rows[rows["_shift"] > 0].sort_values("_shift", ascending=False)["target"].head(places)),
                 list(rows[rows["_shift"] < 0].sort_values("_shift")["target"].head(places))]
    order = []
    for k in range(places):
        for way in ways:
            if k < len(way) and way[k] not in order and (k == 0 or len(order) < most):
                order.append(way[k])
    return {"targets": [entry[t] for t in order], "count": len(entry)}


def controls_differ(result):
    """Whether the two kinds of control differ (D.cutting_response's result.json: cutting against non-targeting
    control cells): {differ, genes_changed, count}, count 1 where they differ."""
    if not isinstance(result, dict) or result.get("status") not in ("detected", "not_detected"):
        return {"differ": None, "count": 0}
    d = result.get("diagnostics") or {}
    differ = result["status"] == "detected"
    return {"differ": differ, "genes_changed": d.get("genes_changed"), "count": int(differ)}


COMPLEX_MAX = 250  # GO cellular components larger than this are compartments (chromatin: 1,422 genes), not complexes
COMPLEXES_SHOWN = 3


def known_complexes(targets, sets, largest=COMPLEX_MAX, shown=COMPLEXES_SHOWN):
    """The known sets ({name: genes}, MSigDB's GO cellular components) of at most largest genes that hold two or more
    of the targets, matched without regard to case: [{set, holds, size}], those holding the most first, then the
    smallest. Prior knowledge beside a set of targets alike (docs/adr/0039, D6), never a test of it."""
    want = {str(t).upper(): t for t in targets}
    out = []
    for name, genes in sets.items():
        if len(genes) > largest:
            continue
        holds = sorted({want[g.upper()] for g in genes if g.upper() in want})
        if len(holds) >= 2:
            out.append({"set": name, "holds": holds, "size": len(genes)})
    out.sort(key=lambda x: (-len(x["holds"]), x["size"], x["set"]))
    return out[:shown]
