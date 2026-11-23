"""A.screen_overview: the screen at a glance, every target by every kind of evidence the first look's runs measured,
for the agent to compare the targets before it goes deep on one (docs/build/open_points_across_pertura_2026-10-28.md,
F3 and F4). It runs no new test: it reads the runs it is given and adds one measure of its own, each target's energy
distance read against groups of control guides drawn to its number of cells (pertura.steps.any_effect.distances). It
names no hit and no next step."""
import json
import os

import numpy as np
import pandas as pd

from .. import policy
from .contract import Contract, Result, register

INPUTS = {"abundance": "B.abundance", "efficacy": "B.efficacy", "states": "C.states_and_composition",
          "similar": "E.similar_targets", "guides": "F.guides_agree", "replicates": "F.replicates_agree",
          "genes": "C.genes", "common": "D.common_response"}
# each column of the table: the input it comes from (None: the screen object, or the overview's own distance)
COLUMNS = {"cells": None, "units": None, "cells_by_unit": None, "guides": None,
           "cells_per_guide_vs_control": "abundance", "target_gene_change": "efficacy", "target_gene_guide": "efficacy",
           "e_distance": "distance", "effect_line": "distance", "past_the_line": "distance",
           "guides_median_r": "guides", "guides_control_pairs_as_high": "guides",
           "units_median_r": "replicates", "unit_pairs": "replicates", "similar": "similar",
           "genes_changed": "genes", "common_response": "common"}


def _read(path):
    if path is None:
        return None
    if isinstance(path, pd.DataFrame):
        return path
    return pd.read_csv(path, sep="\t", keep_default_na=False, na_values=[""])


def _run_of(path):
    """The run a table came from: its folder's name (a run's folder in the jobs root)."""
    return os.path.basename(os.path.dirname(os.path.abspath(path))) if isinstance(path, str) else None


def _target_gene(eff):
    """Per target, its target gene's log2 fold change over all units under its strongest guide (B.efficacy's): the
    guide that moves it furthest the way the guide is expected to (down under CRISPRi or a knockout, up under
    CRISPRa; B.efficacy's expected), or furthest either way where no direction is given."""
    if eff is None or not len(eff):
        return {}
    t = eff[eff["unit"].astype(str) == "all units"] if "unit" in eff and (eff["unit"] == "all units").any() else \
        eff.groupby(["target", "guide"], as_index=False).agg(
            log2fc=("log2fc", "median"), **({"expected": ("expected", "first")} if "expected" in eff else {}))
    t = t.dropna(subset=["log2fc"]).copy()
    way = pd.to_numeric(t["expected"], errors="coerce") if "expected" in t else pd.Series(np.nan, index=t.index)
    t["_along"] = np.where(np.isfinite(way), way * t["log2fc"], t["log2fc"].abs())
    out = {}
    for target, g in t.groupby("target"):
        best = g.sort_values("_along", ascending=False).iloc[0]
        out[str(target)] = (float(best["log2fc"]), str(best["guide"]))
    return out


def _similar(sim, k=3):
    """Per target, its k most similar targets with their correlation; and the targets' order by average linkage of
    the correlation matrix (None with fewer than 3 targets)."""
    if sim is None or not len(sim):
        return {}, None, None
    names = sorted(set(sim["target"].astype(str)) | set(sim["other"].astype(str)))
    M = pd.DataFrame(np.nan, index=names, columns=names)
    for r in sim.itertuples():
        M.loc[str(r.target), str(r.other)] = M.loc[str(r.other), str(r.target)] = float(r.correlation)
    near = {}
    for t in names:
        row = M.loc[t].drop(t).dropna().sort_values(ascending=False).head(k)
        near[t] = "; ".join(f"{o} {v:.2f}" for o, v in row.items())
    order = None
    if len(names) >= 3:
        from scipy.cluster.hierarchy import leaves_list, linkage
        filled = M.fillna(0.0).to_numpy(copy=True)
        np.fill_diagonal(filled, 1.0)
        order = [names[i] for i in leaves_list(linkage(filled, "average"))]
    return near, order, M


def overview_table(screen, tables, strength=None, axis=None):
    """The overview's table: one row per target with cells (the control groups are the reference, not rows);
    columns.json's content: each column's input and state (filled, or not run); and the targets' similarity matrix
    in the table's order (None without E.similar_targets)."""
    from .. import design_fit
    targets = [t for t in screen.targets(1)]
    rows = {t: {"target": t} for t in targets}
    unit = np.asarray(screen.unit).astype(str)
    for t in targets:
        m = screen.label == t
        by = pd.Series(unit[m]).value_counts()
        rows[t].update(cells=int(m.sum()), units=int((by > 0).sum()),
                       cells_by_unit=json.dumps({str(u): int(n) for u, n in by.items()}),
                       guides=len(design_fit.guides_with_cells(screen, t)))
    state = {c: "filled" if src is None else "not run" for c, src in COLUMNS.items()}
    ab = tables.get("abundance")
    if ab is not None and "log2_vs_control_guides" in ab:
        for r in ab.itertuples():
            if str(r.target) in rows:
                rows[str(r.target)]["cells_per_guide_vs_control"] = float(r.log2_vs_control_guides)
        state["cells_per_guide_vs_control"] = "filled"
    own = _target_gene(tables.get("efficacy"))
    if tables.get("efficacy") is not None:
        for t, (v, g) in own.items():
            if t in rows:
                rows[t].update(target_gene_change=v, target_gene_guide=g)
        state["target_gene_change"] = state["target_gene_guide"] = "filled"
    if strength is not None and len(strength):
        for r in strength.itertuples():
            if str(r.target) in rows:
                past = bool(np.isfinite(r.effect_line) and r.e_distance > r.effect_line)
                rows[str(r.target)].update(e_distance=float(r.e_distance), effect_line=float(r.effect_line),
                                           past_the_line=past)
        state["e_distance"] = state["effect_line"] = state["past_the_line"] = "filled"
    gd = tables.get("guides")
    if gd is not None:
        for r in gd.itertuples():
            if str(r.target) in rows:
                rows[str(r.target)].update(guides_median_r=float(r.median_correlation),
                                           guides_control_pairs_as_high=float(r.control_pairs_as_high))
        state["guides_median_r"] = state["guides_control_pairs_as_high"] = "filled"
    rp = tables.get("replicates")
    if rp is not None and len(rp):
        for t, g in rp.groupby("target"):
            if str(t) in rows:
                rows[str(t)].update(units_median_r=float(g["r"].median()), unit_pairs=int(len(g)))
        state["units_median_r"] = state["unit_pairs"] = "filled"
    near, order, sim = _similar(tables.get("similar"))
    if tables.get("similar") is not None:
        for t, words in near.items():
            if t in rows:
                rows[t]["similar"] = words
        state["similar"] = "filled"
    gn = tables.get("genes")
    if gn is not None:
        for r in gn.itertuples():
            if str(r.target) in rows:
                rows[str(r.target)]["genes_changed"] = int(r.genes_changed)
        state["genes_changed"] = "filled"
    cm = tables.get("common")
    if cm is not None:
        col = "e_distance" if "e_distance" in cm else "distance"
        for r in cm.itertuples():
            if str(r.target) in rows:
                rows[str(r.target)]["common_response"] = float(getattr(r, col))
        state["common_response"] = "filled"
    t = pd.DataFrame(list(rows.values()))
    st = tables.get("states")
    shares = []
    if st is not None and len(st):
        name = dict(zip(st["state"].astype(str), st.get("state_name", st["state"]).astype(str)))
        wide = st.pivot_table(index="target", columns="state", values="mean_difference", aggfunc="first")
        first = [s for s in (axis or []) if s in wide.columns]
        rest = sorted((s for s in wide.columns if s not in first), key=lambda s: -wide[s].abs().max())
        for s in first + rest:
            col = f"share:{s}"
            t[col] = t["target"].map(wide[s])
            shares.append(col)
            state[col] = "filled"
        COLUMNS_STATES = {f"share:{s}": name.get(s, s) for s in first + rest}
    else:
        COLUMNS_STATES = {}
    if order:
        place = {x: i for i, x in enumerate(order)}
        t["_order"] = t["target"].map(lambda x: place.get(x, len(place) + 1))
        t = t.sort_values(["_order", "cells"], ascending=[True, False]).drop(columns="_order")
    else:
        t = t.sort_values("cells", ascending=False)
    t = t.reset_index(drop=True)
    for c in COLUMNS:
        if c not in t:
            t[c] = np.nan
    t = t[["target", *COLUMNS, *shares]]
    sim = sim.reindex(index=t["target"], columns=t["target"]) if sim is not None else None
    columns = {c: {"input": COLUMNS[c], "module": INPUTS.get(COLUMNS[c] or "", None if COLUMNS[c] != "distance"
                                                              else "A.screen_overview"),
                   "state": state[c]} for c in COLUMNS}
    columns.update({c: {"input": "states", "module": "C.states_and_composition", "state": "filled",
                        "state_name": n, "the_users_question": c[6:] in (axis or [])}
                    for c, n in COLUMNS_STATES.items()})
    return t, columns, sim


def _states_map(states, screen):
    """The states run's cells on its embedding (cell_states: barcode, umap_1, umap_2, state, state_name), its marker
    dots (marker_dots) and each cell's condition from the screen object, where the design has two or more: the map
    published screens draw first (the knowledge base's FG1, the states), drawn beside the targets. None without the
    run's tables."""
    if not isinstance(states, str):
        return None
    folder = os.path.dirname(os.path.abspath(states))
    if not os.path.exists(os.path.join(folder, "cell_states.tsv")):
        return None
    cells = pd.read_csv(os.path.join(folder, "cell_states.tsv"), sep="\t", keep_default_na=False)
    if not {"barcode", "umap_1", "umap_2", "state"} <= set(cells.columns):
        return None
    # the cells the quality rule left out have no state and no place on the map (GSE267740 on a15df12: their empty
    # coordinates failed the overview each time a states run was in, so its maps were never drawn)
    xy = cells[["umap_1", "umap_2"]].apply(pd.to_numeric, errors="coerce")
    placed = (xy.notna().all(axis=1) & (cells["state"].astype(str) != "")).to_numpy()
    cells, xy = cells[placed], xy[placed]
    dots = (pd.read_csv(os.path.join(folder, "marker_dots.tsv"), sep="\t")
            if os.path.exists(os.path.join(folder, "marker_dots.tsv")) else None)
    names = dict(zip(cells["state"].astype(str), cells["state_name"].astype(str))) if "state_name" in cells else {}
    by = dict(zip(np.asarray(screen.barcodes).astype(str), np.asarray(screen.condition).astype(str)))
    condition = np.array([by.get(str(b), "") for b in cells["barcode"]])
    return {"xy": xy.to_numpy(float), "state": cells["state"].astype(str).to_numpy(),
            "names": {s: s if n in ("", s) else f"{s} {n}" for s, n in names.items()}, "dots": dots,
            "condition": condition if len(set(condition) - {""}) > 1 else None}


TARGET_GENES_LEAST = 3  # targets with their own gene measured, for the map of their changes of each other's genes


def _targets_on_targets(screen, genes, targets):
    """Each target's change of the targets' own genes (the gene test's log2 fold change and q, from the C.genes run's
    own pt.genes call: every gene tested), where TARGET_GENES_LEAST or more of the targets' genes are measured: the
    network among regulators expressed in the cells (the knowledge base's P2.6). None otherwise."""
    if not isinstance(genes, str):
        return None
    from ..blocks import genes_table_file
    path = genes_table_file(os.path.dirname(os.path.abspath(genes)))
    if path is None:
        return None
    own = {str(x): screen.own_gene_of(str(x)) for x in targets}
    own = {x: g for x, g in own.items() if g}
    if len(own) < TARGET_GENES_LEAST:
        return None
    t = pd.read_csv(path, sep="\t")
    if not {"target", "gene", "effect"} <= set(t.columns):
        return None
    t = t[t["target"].astype(str).isin(own) & t["gene"].astype(str).isin(set(own.values()))]
    rows = [x for x in own if x in set(t["target"].astype(str))]
    cols = list(dict.fromkeys(own[x] for x in rows))
    if len(rows) < TARGET_GENES_LEAST:
        return None
    effect = t.pivot_table(index="target", columns="gene", values="effect", aggfunc="first")
    effect = effect.reindex(index=rows, columns=cols)
    from .charts import stars
    q = (t.pivot_table(index="target", columns="gene", values="q", aggfunc="first").reindex(index=rows, columns=cols)
         if "q" in t else None)
    marks = None if q is None else [[stars(v) if np.isfinite(v) else "" for v in r] for r in q.to_numpy(float)]
    return {"M": effect.to_numpy(float), "rows": rows, "cols": cols, "marks": marks,
            "q": None if q is None else q.to_numpy(float), "own": own}


def network_biology(network, level=0.05, most=25, genes=6, knockout=False):
    """The network in lines: each target's changes of the other targets' own genes at q < level (its own gene left
    out), the target changing the most first, each gene with its log2 fold change; under knockout, what a target's
    own gene says of its knockout (PF7), where the network is read."""
    if not network or network.get("q") is None:
        return []
    if knockout:
        from .b import KNOCKOUT_READING
        return [KNOCKOUT_READING] + network_biology(network, level, most, genes)
    found = []
    for i, t in enumerate(network["rows"]):
        hits = sorted(((g, network["M"][i][j]) for j, g in enumerate(network["cols"])
                       if g != network["own"].get(t) and network["q"][i][j] < level and np.isfinite(network["M"][i][j])),
                      key=lambda h: -abs(h[1]))
        if hits:
            found.append((len(hits), f"{t}: " + ", ".join(f"{'lowers' if e < 0 else 'raises'} {g} ({e:+.2f})"
                                                            for g, e in hits[:genes])
                          + (f", and {len(hits) - genes} more" if len(hits) > genes else "")))
    found.sort(key=lambda f: -f[0])
    if not found:
        return []
    return ([f"the targets' own genes each target changes at q < {level} (log2 fold change; C.genes' test of every "
             "gene):"] + [w for _, w in found[:most]]
            + ([f"{len(found) - most} more targets: the C.genes run's table holds them"] if len(found) > most else []))


@register("A.screen_overview", Contract(
    serves="A.screen_overview",
    applies_when="after the first look: every target by every kind of evidence its runs measured",
    needs="the screen object, and the first look's runs as inputs (each optional): B.abundance, B.efficacy, "
          "C.states_and_composition, E.similar_targets, F.guides_agree, F.replicates_agree; C.genes and "
          "D.common_response when they have run",
    returns="per target: its cells, the units holding it and its cells in each, its guides with 10 cells; its "
            "cells per guide against the control guides' (B.abundance); its target gene's change under its strongest "
            "guide, the one moving it furthest the expected way (B.efficacy); its energy distance from the control "
            "cells of its own samples (pertpy's edistance on the PCA of log-normalised highly variable genes, as "
            "C.any_effect computes it, without the permutation test) beside the 95th percentile of the same distance "
            "for random groups of control guides, each of a target's number of guides, the line it is read against; "
            "the target and its groups drawn to one number of cells: the smallest of its cells, 1,000, and what "
            "three of the groups hold; its guides' agreement (F.guides_agree) "
            "and its units' (F.replicates_agree: the median correlation of its profile between units); its three "
            "most similar targets (E.similar_targets); its share of each state minus the control cells' "
            "(C.states_and_composition), the states of the user's question first; C.genes' and "
            "D.common_response's columns once given. columns.json gives each column's input and whether it is "
            "filled; the figure, composed of the field's charts: every target's distance with its own line, its "
            "target gene's change against its distance, the targets' similarity with their measures beside it, "
            "their states' shares, the cells on the states run's embedding by state with each state's markers and "
            "by condition where the design has two or more, and each target's change of the targets' own genes "
            "where three or more are measured (C.genes)",
    fails_when=[("a target with few cells reads a larger energy distance from its few cells alone",
                 "the line from control-guide groups drawn to the same number of cells"),
                ("a column of a module not run reads as no effect",
                 "columns.json: each column's state, filled or not run")],
    tested_against="the synthetic screen: the planted target past its line, the control-guide groups inside theirs",
    inputs={"abundance": "a B.abundance run (its result)", "efficacy": "a B.efficacy run (its result)",
            "states": "a C.states_and_composition run (its composition, the result)",
            "similar": "an E.similar_targets run (its result)", "guides": "an F.guides_agree run (its result)",
            "replicates": "an F.replicates_agree run (its result)", "genes": "a C.genes run (its result)",
            "common": "a D.common_response run (its result)",
            "axis": "the states of the user's question ([state IDs]), first among the states' columns",
            "previous": "an earlier overview run (its targets): its distances and lines are kept, not computed again"}))
def screen_overview(screen, design, out_dir, abundance=None, efficacy=None, states=None, similar=None, guides=None,
                    replicates=None, genes=None, common=None, axis=None, previous=None):
    given = {"abundance": abundance, "efficacy": efficacy, "states": states, "similar": similar, "guides": guides,
             "replicates": replicates, "genes": genes, "common": common}
    tables = {k: _read(v) for k, v in given.items() if v is not None}
    note, calls = None, []
    kept = _read(previous)
    if kept is not None and {"e_distance", "effect_line"} <= set(kept.columns):
        strength = kept[["target", "e_distance", "effect_line"]].copy()
    else:
        try:
            from ..steps import any_effect
            strength, calls = any_effect.distances(screen)
        except ImportError as e:  # scanpy or pertpy missing: the distance column says so
            strength, note = None, f"no distances: {e}"
    table, columns, sim = overview_table(screen, tables, strength, axis)
    for k, v in given.items():
        for c in columns.values():
            if c.get("input") == k and v is not None:
                c["run"] = _run_of(v)
    with open(os.path.join(out_dir, "columns.json"), "w", encoding="utf-8") as fh:
        json.dump(columns, fh, indent=1)
    path = os.path.join(out_dir, "figure.png")
    network = _targets_on_targets(screen, genes, list(table["target"]))
    checks, legend = draw(table, columns, path, sim, design_stages(screen, design, table), tables.get("states"),
                          _states_map(states, screen), network, matching=screen.matching_words)
    runs = {k: _run_of(v) for k, v in given.items() if v is not None}
    legend = [{**x, "run": runs.get(x["input"])} for x in legend]  # the run each panel reads, beside its own
    past = table[table["past_the_line"] == True]  # noqa: E712 (NaN where not computed)
    lineless = int(table["effect_line"].isna().sum()) if strength is not None else 0
    words = (f"{len(table)} targets by {sum(1 for c in columns.values() if c['state'] == 'filled')} columns of "
             f"evidence; {len(past)} past their line of control-guide groups drawn to their own cells"
             + (f", {lineless} without a line (too few control-guide groups hold their cells)" if lineless else "")
             if strength is not None else f"{len(table)} targets; {note}")
    missing = [INPUTS[k] for k in INPUTS if given.get(k) is None]
    if missing:
        words += "; not given: " + ", ".join(missing)
    diagnostics = {"targets": int(len(table)), "past_the_line": sorted(map(str, past["target"])),
                   "without_a_line": sorted(map(str, table.loc[table["effect_line"].isna(), "target"]))
                   if strength is not None else [],
                   "columns_filled": sorted(c for c, v in columns.items() if v["state"] == "filled"),
                   "network_targets": len(network["rows"]) if network else 0,  # targets whose own gene is measured
                   "not_given": missing, "axis": list(axis or []), "package_calls": calls, "legend": legend,
                   **({"note": note} if note else {})}
    pooled = sorted(set(screen.condition) - {""})
    if len(pooled) > 1:  # the default runs it reads pool them; a design may read them apart (the fresh review, #1)
        diagnostics["conditions_pooled"] = pooled
        if screen.crossed:  # its comparisons still match each target's controls within its condition
            diagnostics["matched_within_conditions"] = True
    from ..record import modalities
    return Result("measured", table, diagnostics, path, checks, words, tables={"targets": table},
                  biology=network_biology(network, knockout="crispr_ko" in modalities(design)))


def design_stages(screen, design, table):
    """The screen's design as published screens draw it first (library, cells, samples, readout), from the confirmed
    design and the screen object: the targets, their guides holding cells, the control guides holding cells (by their
    kind), the cells and units told apart (the unassigned left out, and those quality control leaves out where the
    screen object is loaded) in their samples, how the units were told apart, the conditions, and the measurements
    beside the RNA; counted, never a sentence on the replicates."""
    from ..design_gate import condition_factors
    from .data import UNASSIGNED, value
    modality = value(design, "perturbation.modality")
    modes = ", ".join(map(str, modality)) if isinstance(modality, list) else str(modality or "")
    def n(k, word):
        return f"{k} {word}" + ("" if k == 1 else "s")
    library = [n(len(table), "target") + (f" ({modes.replace('_', ' ')})" if modes else "")]
    if len(table) and "guides" in table:
        library.append(n(int(table["guides"].median()), "guide") + " each with cells (median)")
    g = screen.guides
    controls = set(g.loc[~g["kind"].astype(str).isin(["targeting", "unknown"]), "guide"].astype(str))
    library.append(n(len(controls & set(np.asarray(screen.top_guide).astype(str))), "control guide") + " with cells")
    unit = pd.Series(np.asarray(screen.unit).astype(str))
    label = getattr(screen, "label", None)  # a loaded screen object carries the design's quality rule in its labels
    if label is not None:
        from .. import quality
        unit = unit[np.asarray(label).astype(str) != quality.FAILED]
    told = unit[~unit.str.endswith(UNASSIGNED)]
    context = value(design, "experiment.context")
    cells = ([str(context).replace("_", " ")] if context else []) + [
        f"{len(told):,} cells with a unit" + (" after quality control" if label is not None else "")]
    column = value(design, "units.unit_column")
    # counted, not composed: whether the units are biological replicates is the agent's to say in each result's
    # samples sentence (docs/adr/0041, step 2; the panel once wrote "each a cell (no biological replicate ...)")
    n_samples = len(set(np.asarray(screen.sample).astype(str))) if getattr(screen, "sample", None) is not None else 0
    samples = [n(told.nunique(), "unit") + (f" in {n(n_samples, 'sample')}" if n_samples and n_samples !=
                                             told.nunique() else "")]
    if column:
        samples.append(f"told apart by the column '{column}'")
    for f in condition_factors(value(design, "experiment.conditions")):
        if f["levels"]:
            samples.append(("" if f["name"] == "condition" else f"{f['name'].replace('_', ' ')}: ")
                           + ", ".join(f["levels"]))
    readout = ["single-cell RNA-seq", "guide capture"]
    if screen.meta.get("antibody_features"):
        readout.append(f"{len(screen.meta['antibody_features'])} antibody counts")
    return [("Library", library), ("Cells", cells), ("Samples", samples), ("Readout", readout)]


def design_stages_from_files(screen_dir, design):
    """design_stages from a built screen object's files alone (guides.tsv, cells.tsv.gz, screen.json), with no counts
    loaded: for the report's Figure 1 where no A.screen_overview ran (session 13)."""
    import types
    from ..design_fit import MIN_CELLS
    guides = pd.read_csv(os.path.join(screen_dir, "guides.tsv"), sep="\t")
    unit = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", usecols=["unit"])["unit"]
    with open(os.path.join(screen_dir, "screen.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    g = guides.assign(cells=pd.to_numeric(guides["cells"], errors="coerce").fillna(0) if "cells" in guides else 1)
    tg = g[g["kind"].astype(str) == "targeting"]
    targets = sorted(set(tg.loc[tg["cells"] > 0, "target"].astype(str)))
    per = tg[tg["cells"] >= MIN_CELLS].groupby("target")["guide"].nunique()
    screen = types.SimpleNamespace(guides=guides, top_guide=g.loc[g["cells"] > 0, "guide"].astype(str).to_numpy(),
                                   unit=unit.astype(str).to_numpy(), meta=meta)
    return design_stages(screen, design, pd.DataFrame({"target": targets,
                                                       "guides": [int(per.get(t, 0)) for t in targets]}))


def _state_marks(t, shares, states):
    """The significance marks of the states' shares (states by targets, as the figure draws them), from the states
    run's q; None where its table gives no q."""
    from .charts import stars
    if states is None or "q" not in states:
        return None
    q = states.pivot_table(index="target", columns="state", values="q", aggfunc="first")
    q.index, q.columns = q.index.astype(str), q.columns.astype(str)
    q = q.reindex(index=t["target"].astype(str), columns=[c[len("share:"):] for c in shares])
    return [[stars(v) if np.isfinite(v) else "" for v in q[s]] for s in q.columns]


def draw(table, columns, path, sim=None, stages=None, states=None, maps=None, network=None,
         matching="the control cells of its own samples, in its mix"):
    """The overview's figure, composed of the field's charts (charts.py, laid out and checked by
    figure_series.compose): a, the screen's design (stages, design_stages); then every target's energy distance with
    its own line, the control-guide groups drawn to its cells; its target gene's change against its distance; the
    correlation of every two targets' profiles, clustered, with each target's cells, target gene's change, distance
    over its line and its guides' and units' agreement beside it; each target's share of each state minus its control
    cells', marked where the states run (states, its table) found it; the cells on the states run's embedding by state
    with the states' markers, and by condition (maps, _states_map); each target's change of the targets' own genes
    (network, _targets_on_targets). Returns the checks, the layout checks' issues
    among them, and the legend: each panel drawn, in its letter's order, with what it shows, its n and the input it
    reads (None: the screen object and the overview's own distance)."""
    from . import charts, figure_series, figures
    t = table
    rows = []
    first = []
    legend = []
    if stages:
        rows.append((28, [(1, lambda p: charts.design_panel(stages, fig=p))]))
        legend.append({"input": None, "text": (f"the screen's design, from the confirmed design and the screen object: "
                                               f"its {len(t)} targets, their guides and control guides holding "
                                               "cells, its samples and conditions, and what was measured")})
    dist = t.dropna(subset=["e_distance"])
    if len(dist):
        first.append((1.3, lambda p: charts.ranked_targets(dist, "e_distance", None, "Energy distance vs control "
                                                            "cells", "", own_line="effect_line", fig=p)))
        past = int((dist["past_the_line"] == True).sum()) if "past_the_line" in dist else 0  # noqa: E712
        lineless = int(dist["effect_line"].isna().sum()) if "effect_line" in dist else len(dist)
        legend.append({"input": None, "text": (
            f"each target's energy distance from its control cells (pertpy, on principal components; "
            f"{matching}), {len(dist)} targets, ranked; its line (a black mark) is the 95th "
            f"percentile of the same distance for groups of control guides drawn to its number of cells, and {past} "
            f"of the targets are past their line"
            + (f"; {lineless} have no line, as too few control-guide groups hold their cells (a ring)" if lineless
               else ""))})
    both = t.dropna(subset=["target_gene_change", "e_distance"])
    if len(both) >= 3:
        first.append((0.7, lambda p: charts.two_scores(both, "target_gene_change", "e_distance", None, "",
                                                        "Target gene, log2 FC vs control cells",
                                                        "Energy distance vs control cells", log_y=True, fig=p)))
        legend.append({"input": "efficacy", "text": (
            f"each target's own gene, its log2 fold change against the control cells (B.efficacy), against its "
            f"energy distance on a log scale, {len(both)} targets")})
    if first:
        rows.append((64, first))
    if sim is not None and np.isfinite(sim.to_numpy(float)).sum() > len(sim):
        side = pd.DataFrame({"Cells": t["cells"].to_numpy(float),
                             "Target gene (log2 FC)": t["target_gene_change"].to_numpy(float),
                             "Distance over its line": (t["e_distance"] / t["effect_line"]).to_numpy(float),
                             "Guides agree (r)": t["guides_median_r"].to_numpy(float),
                             "Replicates agree (r)": t["units_median_r"].to_numpy(float)}, index=t["target"])
        side = side.loc[:, side.notna().any()]
        C = sim.reindex(index=t["target"], columns=t["target"])
        rows.append((max(70, min(150, 2.4 * len(C) + 30)),
                     [(1, lambda p: charts.target_panel(C, side, label="Correlation of two targets' profiles",
                                                        fig=p))]))
        legend.append({"input": "similar", "text": (
            f"the correlation of every two targets' profiles (E.similar_targets), clustered, {len(C)} targets; beside "
            f"it, for each target: {', '.join(c[0].lower() + c[1:] for c in side.columns)}")})
    shares = [c for c in t.columns if c.startswith("share:")]
    if shares:
        M = t[shares].to_numpy(float, copy=True).T
        names = [columns[c].get("state_name", c[6:]) + (" *" if columns[c].get("the_users_question") else "")
                 for c in shares]
        marks = _state_marks(t, shares, states)
        rows.append((max(45, min(110, 2.6 * len(shares) + 32)),
                     # the values bound now: the panel is drawn after the network's block has its own M
                     [(1, lambda p, M=M, names=names, marks=marks, cols=list(t["target"]): charts.heatmap(
                         M, names, cols, None, "", "State share minus control share", marks=marks, cap=98,
                         fig=p))]))
        marked = any(columns[c].get("the_users_question") for c in shares)
        dots = len(t) > charts.MARKS_AS_DOTS
        legend.append({"input": "states", "text": (
            f"each target's share of the cells in each state minus its control cells' share "
            f"(C.states_and_composition), {len(shares)} states by {len(t)} targets; the scale stops at the 98th "
            "percentile of the differences"
            + ("" if marks is None else "; a dot marks the states run's q below 0.05" if dots else
               "; the states run's q marked *, ** and *** below 0.05, 0.01 and 0.001")
            + ("; * after a state's name marks the states of the user's question" if marked else ""))})
    if maps is not None:
        row = [(1, lambda p: charts.embedding_states(maps["xy"], maps["state"], maps["names"], None, "", fig=p))]
        if maps["dots"] is not None and len(maps["dots"]):
            row.append((1.3, lambda p: charts.marker_dots(maps["dots"], None, "", fig=p)))
        rows.append((62, row))
        legend.append({"input": "states", "text": (
            f"the cells on the states run's embedding (UMAP), coloured by their {len(set(maps['state']))} states, "
            f"{len(maps['state']):,} cells" + ("; beside it, each state's marker genes: dot size the share of its "
                                               "cells expressing the gene, colour its mean scaled across states"
                                               if len(row) > 1 else ""))})
        if maps["condition"] is not None:
            named = {c: c for c in set(maps["condition"]) if c}
            shown = maps["condition"] != ""
            rows.append((62, [(1, lambda p: charts.embedding_states(maps["xy"][shown], maps["condition"][shown],
                                                                   named, None, "", fig=p))]))
            legend.append({"input": "states", "text": (
                f"the same cells coloured by condition, {len(named)} conditions, {int(shown.sum()):,} cells")})
    if network is not None:
        M = network["M"]
        rows.append((max(45, min(110, 2.6 * len(network["rows"]) + 32)),
                     [(1, lambda p, M=M: charts.heatmap(M, network["rows"], network["cols"], None, "",
                                                        "log2 fold change vs control cells", marks=network["marks"],
                                                        cap=98, fig=p))]))
        legend.append({"input": "genes", "text": (
            f"each target's change of the targets' own genes (C.genes' test of every gene: log2 fold change against "
            f"control cells), {len(network['rows'])} targets by {len(network['cols'])} genes; the q marked *, ** and "
            "*** below 0.05, 0.01 and 0.001; the scale stops at the 98th percentile")})
    title = "The screen at a glance: each target's distance, target gene, similarity and states"
    if not rows:  # nothing measured to draw: one empty chart saying so
        return figures.bars(np.zeros(1), ["none"], path, "No target with a measure to draw", "targets"), []
    issues, letters = figure_series.compose(rows, path, title)
    legend = [{"letter": k, **x} for k, x in zip(letters, legend)]
    return charts.checks(issues, reference=True, zero=bool(shares) or None, direction=True), legend
