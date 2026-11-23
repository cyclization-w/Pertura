"""The charts published screens draw (docs/adr/0022, section 8; docs/build_goal_adr0022.md, step 4), as generic
functions on tidy tables. Each draws at its final width (89 or 183 mm) in the modules' style (figures.STYLE), saves a
PNG and a PDF with editable text, and returns the layout checks' issues (figures.check_figure; empty when none).

The charts: all targets ranked against the control cells; abundance per guide with clonal bursts marked; a volcano;
a target-by-gene (or target-by-program) heatmap, clustered on both axes; an embedding by named state; a marker dot
plot; a scatter of two scores with the top targets labelled; a density difference map (a target's cells minus the
control cells'); a signature's change within each state; a pathway dot plot; knockdown against baseline expression;
states along pseudotime with each target's density. One colour per state across all charts (state_colours).
Significance marks are allowed in plots; the test, n and error bars go in the caption.
"""
import os

import numpy as np
import pandas as pd

from . import figures

MM = figures.MM
SINGLE, DOUBLE = figures.SINGLE, figures.DOUBLE
CONTROL = figures.CONTROL_GREY
STATE_PALETTE = ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB", "#000000",
                 "#EE8866", "#44BB99", "#99DDFF", "#FFAABB", "#77AADD", "#EEDD88", "#AAAA00", "#882255",
                 "#117733", "#332288", "#DDCC77", "#CC6677", "#88CCEE", "#999933", "#44AA99", "#661100",
                 "#6699CC", "#AA4499", "#DDDDDD", "#BB5566", "#004488", "#997700", "#EE99AA", "#555555"]


def state_colours(states):
    """One colour per state, fixed by the states' sorted names, so every chart of a session shows a state alike
    (32 distinct colours before any repeats)."""
    names = sorted({str(s) for s in states})
    return {s: STATE_PALETTE[i % len(STATE_PALETTE)] for i, s in enumerate(names)}


def checks(issues, reference=True, zero=None, direction=True):
    """A module's figure checks for a chart: what it shows, and the layout checks' issues."""
    return {"reference_shown": reference, "zero_centred": zero, "direction_labelled": direction,
            "layout_issues": list(issues)}


def composition(shares, states, path, title, control="control", names=None, matched=None, colours=None, fig=None):
    """Stacked bars of each group's state shares (shares: groups by states), the control row on top, one colour per
    state (state_colours), the states named by their markers (names) in the legend. matched: {group: its own control
    cells' shares} (each group against the control cells of its own samples): each group's bar is drawn above its
    control cells' bar, in lighter colours, and there is no shared control row. colours: {state: colour}, to keep
    the session's colours when only some states are drawn."""
    plt = figures._plt()
    col = colours or state_colours(states)
    fig, own = _canvas(plt, fig, (SINGLE, _height(len(shares) * (2 if matched else 1), 2.6 if matched else 3.4,
                                                  low=45)))
    ax = fig.add_subplot()
    if matched:
        ys, names_y = [], []
        for i, g in enumerate(shares.index):
            for k, (row, alpha) in enumerate(((shares.loc[g], 1.0), (matched[g], 0.4))):
                y = i * 2.5 + k
                left = 0.0
                for st in states:
                    v = float(row.get(st, 0.0))
                    ax.barh(y, v, left=left, height=0.85, color=col[st], alpha=alpha, linewidth=0)
                    left += v
                ys.append(y)
                names_y.append(str(g) if k == 0 else "control cells")
        ax.set_yticks(ys)
        ax.set_yticklabels(names_y)
        ax.set_ylim(max(ys) + 0.7, -0.7)
        ax.set_xlim(0, 1)
    else:
        figures.composition_bars(ax, shares, list(states), col, control=control)
    handles = [plt.Rectangle((0, 0), 1, 1, color=col[s]) for s in states]
    ax.legend(handles, [(names or {}).get(s, s) for s in states], loc="upper left", bbox_to_anchor=(1.0, 1.0),
              fontsize=5, handlelength=0.8)
    ax.set_xlabel("Share of cells")
    return _done(plt, fig, own, path, title)


def stars(q):
    """Significance marks: *** below 0.001, ** below 0.01, * below 0.05."""
    return "***" if q < 0.001 else "**" if q < 0.01 else "*" if q < 0.05 else ""


def _finish(plt, fig, path, title):
    issues = figures.check_figure(fig)
    fig.savefig(path, metadata={"Title": title}, dpi=300)
    fig.savefig(os.path.splitext(path)[0] + ".pdf", metadata={"Title": title})
    plt.close(fig)
    return issues


def _plain_log(axis):
    """A log axis labelled in plain numbers at 1, 2 and 5 of each decade (0.05, 0.1, 0.2), without minor labels."""
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
    axis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    axis.set_minor_formatter(NullFormatter())


def _canvas(plt, fig, size, layout=None):
    """The chart's own figure at size (inches), or the panel it is drawn into: a subfigure of a figure composed by
    figure_series.compose, which sets its size and layout."""
    return (plt.figure(figsize=size, layout=layout), True) if fig is None else (fig, False)


def _done(plt, fig, own, path, title):
    """A chart on its own figure is laid out, checked and saved; a panel is checked with the figure it is part of."""
    if not own:
        return []
    if fig.get_layout_engine() is None:
        fig.tight_layout(pad=0.4)
    return _finish(plt, fig, path, title)


def _height(n, per=2.6, low=40, high=170):
    return float(np.clip(n * per + 18, low, high)) * MM


def _width(n_groups):
    """One column for up to 30 groups, two beyond: a figure is 89 or 183 mm wide, never between."""
    return SINGLE if n_groups <= 30 else DOUBLE


def _after_layout(fig, place):
    """Runs place(in_layout) once the chart's place is final: at once on a chart's own figure; for a panel, after the
    composed figure is laid out (figure_series.compose), as a panel moves until then. A panel's late labels stay out
    of the layout, so placing them moves nothing."""
    from matplotlib.figure import SubFigure
    if isinstance(fig, SubFigure):
        root = fig.figure
        root.__dict__.setdefault("_after_layout", []).append(lambda: place(False))
    else:
        place(True)


def _labels(ax, fig, points, fontsize=5):
    """Name points (x, y, text) in order, to the point's right, or to its left where the right would leave the
    chart's own area (its panel); a label that fits neither way, or would overlap one already placed, is left out."""
    points = list(points)

    def place(in_layout):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        area = fig.bbox
        placed = []
        for x, y, text in points:
            for dx, ha in ((2, "left"), (-2, "right")):
                a = ax.annotate(str(text), (x, y), fontsize=fontsize, xytext=(dx, 1), textcoords="offset points",
                                ha=ha)
                a.set_in_layout(in_layout)
                box = a.get_window_extent(renderer)
                inside = (box.x0 >= area.x0 and box.x1 <= area.x1 and box.y0 >= area.y0 and box.y1 <= area.y1)
                if inside and not any(box.overlaps(b) for b in placed):
                    placed.append(box)
                    break
                a.remove()
    _after_layout(fig, place)


def _order(M):
    """Rows and columns of a matrix ordered by hierarchical clustering (average linkage, Euclidean)."""
    from scipy.cluster.hierarchy import leaves_list, linkage
    A = np.nan_to_num(np.asarray(M, float))
    rows = leaves_list(linkage(A, "average")) if A.shape[0] > 2 else np.arange(A.shape[0])
    cols = leaves_list(linkage(A.T, "average")) if A.shape[1] > 2 else np.arange(A.shape[1])
    return rows, cols


def ranked_targets(table, value, path, ylabel, title, q=None, control_band=None, top=80, control_line=None,
                   muted=None, own_line=None, fig=None):
    """All targets ranked by `value` (table: target, value, and q if given): one bar each, significance marks above,
    the control guides' range as a grey band (control_band: (low, high)) or a dashed line at zero; control_line:
    (value, its name in the key), a dotted line such as the control-guide groups' 95th percentile; muted: (targets,
    their name in the key), drawn in light grey (a reading that cannot rest on them); own_line: a column holding each
    target's own line (such as control-guide groups drawn to its number of cells), a short black mark across its
    bar, the bars not past their own line in light grey and a target without one marked with a ring. The key above
    the plot names what the chart greys or marks: "Threshold", "Below threshold", "No threshold", "Control guides
    (range)", and the names given."""
    from matplotlib.patches import Patch
    plt = figures._plt()
    t = table.sort_values(value, ascending=False).head(top)
    fig, own = _canvas(plt, fig, (_width(len(t)), 58 * MM))
    ax = fig.add_subplot()
    x = np.arange(len(t))
    muted_targets, muted_name = muted if muted is not None else ((), "")
    light, light_names, key = set(muted_targets) & set(t["target"]), [], []
    if light:
        light_names.append(muted_name)
    if own_line is not None:
        line = pd.to_numeric(t[own_line], errors="coerce").to_numpy(float)
        v = pd.to_numeric(t[value], errors="coerce").to_numpy(float)
        short = {g for g, a, b in zip(t["target"], v, line) if np.isfinite(b) and not a > b}
        light |= short
        light_names += ["Below threshold"] if short else []
        ax.hlines(line, x - 0.42, x + 0.42, color="black", lw=0.8, zorder=3)
        none = ~np.isfinite(line)
        ax.scatter(x[none], v[none], s=10, facecolors="none", edgecolors="black", linewidths=0.5, zorder=4)
        key.append((plt.Line2D([], [], color="black", lw=0.8), "Threshold"))
        if none.any():
            key.append((plt.Line2D([], [], marker="o", ls="", ms=3, markerfacecolor="none", markeredgecolor="black",
                                   markeredgewidth=0.5), "No threshold"))
    if light:
        key.append((Patch(color="0.78", linewidth=0), "; ".join(n for n in light_names if n)))
    ax.bar(x, t[value], color=["0.78" if v in light else "0.25" for v in t["target"]], width=0.7,
           linewidth=0)
    if control_band is not None:
        ax.axhspan(control_band[0], control_band[1], color="0.88", zorder=0, linewidth=0)
        key.append((Patch(color="0.88", linewidth=0), "Control guides (range)"))
    ax.axhline(0, color="0.45", lw=0.5, ls="--", zorder=1)
    if control_line is not None:  # named in the key only when given a name
        ax.axhline(control_line[0], color="#0072B2", lw=0.8, ls=":", zorder=2)
        if control_line[1]:
            key.append((plt.Line2D([], [], color="#0072B2", lw=0.8, ls=":"), control_line[1]))
    key = [(h, n) for h, n in key if n]
    if key:
        _key(ax, *zip(*key))
    if q is not None:
        span = float(np.nanmax(np.abs(t[value]))) or 1.0
        for i, (v, qq) in enumerate(zip(t[value], t[q])):
            mark = stars(qq)
            if mark:
                ax.text(i, v + 0.02 * span * (1 if v >= 0 else -4), mark, ha="center", va="bottom", fontsize=5)
    ax.set_xticks(x)
    ax.set_xticklabels(t["target"], rotation=90, fontsize=5 if len(t) > 40 else 6)
    ax.set_xlim(-0.7, len(t) - 0.3)
    ax.set_ylabel(ylabel)
    return _done(plt, fig, own, path, title)


def guide_abundance(table, path, title, burst=None, fig=None):
    """Cells per guide for each target (table: target, guide, cells; control guides with target "control guides"),
    the control guides' range as a grey band; a guide holding a clonal burst (burst: set of guides) drawn hollow."""
    plt = figures._plt()
    t = table.copy()
    t["log2"] = np.log2(t["cells"] + 1)
    ctrl = t[t["target"] == "control guides"]["log2"]
    order = (t[t["target"] != "control guides"].groupby("target")["log2"].median()
             .sort_values(ascending=False).index.tolist())
    groups = (["control guides"] if len(ctrl) else []) + order
    fig, own = _canvas(plt, fig, (_width(len(groups)), 58 * MM))
    ax = fig.add_subplot()
    if len(ctrl):
        ax.axhspan(ctrl.min(), ctrl.max(), color="0.9", zorder=0, linewidth=0)
        ax.axhline(ctrl.median(), color="0.45", lw=0.5, ls="--", zorder=1)
    rng = np.random.default_rng(0)
    for i, g in enumerate(groups):
        s = t[t["target"] == g]
        jit = rng.uniform(-0.18, 0.18, len(s))
        hollow = s["guide"].isin(burst or set()).to_numpy()
        colour = CONTROL if g == "control guides" else "0.15"
        ax.scatter(i + jit[~hollow], s["log2"].to_numpy()[~hollow], s=6, color=colour, linewidths=0, zorder=3)
        if hollow.any():
            ax.scatter(i + jit[hollow], s["log2"].to_numpy()[hollow], s=9, facecolors="none", edgecolors="#D55E00",
                       linewidths=0.6, zorder=3)
    ax.set_xticks(range(len(groups)))
    if burst and t["guide"].isin(burst).any():  # the key: what the hollow mark means
        ax.legend([plt.Line2D([], [], marker="o", ls="", ms=3, markerfacecolor="none", markeredgecolor="#D55E00",
                              markeredgewidth=0.6)], ["Clonal burst"], loc="upper right",
                  fontsize=5, frameon=False, handletextpad=0.2)
    ax.set_xticklabels(groups, rotation=90, fontsize=5 if len(groups) > 40 else 6)
    ax.set_xlim(-0.7, len(groups) - 0.3)
    ax.set_ylabel("Cells per guide, log2(n + 1)")
    return _done(plt, fig, own, path, title)


def volcano(table, path, title, effect="effect", q="q", gene="gene", label_top=10, threshold=0.05,
            xlabel="log2 fold change vs control cells", fig=None):
    """One comparison's genes (table: gene, effect, q): effect against -log10 q, the changed genes in colour (down
    blue, up red), the top label_top named."""
    plt = figures._plt()
    t = table.copy()
    t[effect], t[q] = pd.to_numeric(t[effect], errors="coerce"), pd.to_numeric(t[q], errors="coerce")
    t = t.dropna(subset=[effect, q])
    t["y"] = -np.log10(t[q].clip(lower=1e-300))
    sig = t[q] < threshold
    fig, own = _canvas(plt, fig, (SINGLE, 62 * MM))
    ax = fig.add_subplot()
    ax.scatter(t.loc[~sig, effect], t.loc[~sig, "y"], s=3, color="0.75", linewidths=0, rasterized=True)
    ax.scatter(t.loc[sig & (t[effect] < 0), effect], t.loc[sig & (t[effect] < 0), "y"], s=4, color="#2166AC",
               linewidths=0)
    ax.scatter(t.loc[sig & (t[effect] > 0), effect], t.loc[sig & (t[effect] > 0), "y"], s=4, color="#B2182B",
               linewidths=0)
    ax.axhline(-np.log10(threshold), color="0.6", lw=0.5, ls=":")
    ax.axvline(0, color="0.6", lw=0.5, ls="--")
    top = t[sig].sort_values("y", ascending=False).head(label_top)
    _labels(ax, fig, zip(top[effect], top["y"], top[gene]))
    shown = {f"{way} (adj. p < {threshold:g})": c for way, c, m in
             (("Down", "#2166AC", sig & (t[effect] < 0)), ("Up", "#B2182B", sig & (t[effect] > 0))) if m.any()}
    if shown:
        _key_above(ax, shown)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("−log10 adjusted p")
    return _done(plt, fig, own, path, title)


MARKS_AS_DOTS = 40  # columns beyond which a heatmap's cells are too narrow for its marks' text: a dot marks them


def diverging_colour_bar(fig, im, label, capped=False, **where):
    """A colour bar for a scale centred on zero: a few ticks set symmetrically about zero, the label wrapped to two
    lines where it is long, so neither runs past the bar."""
    import textwrap
    from matplotlib.ticker import MaxNLocator
    cb = fig.colorbar(im, extend="both" if capped else "neither", **where)
    cb.locator = MaxNLocator(nbins=4, symmetric=True)
    cb.set_label(textwrap.fill(str(label), 30), fontsize=6)
    cb.ax.tick_params(labelsize=5)
    return cb


def heatmap(M, rows, cols, path, title, label, marks=None, cluster=True, width=None, cap=None, fig=None):
    """A matrix (rows x cols) on a diverging scale centred on zero, rows and columns clustered; marks: the same
    shape, a significance mark per cell (strings), drawn as a dot where the columns are too narrow for text
    (MARKS_AS_DOTS); cap: a percentile of the absolute values at which the scale stops (the colour bar's ends then
    mark that larger values exist), so a few extreme values do not wash out the rest."""
    plt = figures._plt()
    M = np.asarray(M, float)
    r, c = _order(M) if cluster else (np.arange(M.shape[0]), np.arange(M.shape[1]))
    M = M[r][:, c]
    rows, cols = [rows[i] for i in r], [cols[j] for j in c]
    lim = float(np.nanmax(np.abs(M))) if np.isfinite(M).any() else 1.0
    capped = False
    if cap is not None and np.isfinite(M).any():
        at = float(np.nanpercentile(np.abs(M[np.abs(M) > 0]), cap)) if (np.abs(M) > 0).any() else lim
        capped, lim = at < lim, min(lim, at) or lim
    w = width or (DOUBLE if len(cols) > 18 else SINGLE)
    fig, own = _canvas(plt, fig, (w, _height(len(rows))), layout="constrained")
    ax = fig.add_subplot()
    im = ax.imshow(M, cmap="RdBu_r", vmin=-lim, vmax=lim, aspect="auto", interpolation="nearest")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([figures.short_name(x) for x in cols], rotation=90, fontsize=5 if len(cols) > 30 else 6)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=5 if len(rows) > 30 else 6)
    if marks is not None:
        mk = np.asarray(marks, dtype=object)[r][:, c]
        dark = np.abs(np.nan_to_num(M)) > 0.6 * (lim or 1.0)  # a mark on a dark cell is white
        where = [(i, j) for i in range(mk.shape[0]) for j in range(mk.shape[1]) if mk[i, j]]
        if mk.shape[1] > MARKS_AS_DOTS:
            if where:
                ax.scatter([j for _, j in where], [i for i, _ in where], s=1.5, linewidths=0, zorder=3,
                           c=["white" if dark[i, j] else "black" for i, j in where])
        else:
            for i, j in where:
                ax.text(j, i, mk[i, j], ha="center", va="center", fontsize=5,
                        color="white" if dark[i, j] else "black")
    diverging_colour_bar(fig, im, label, capped, ax=ax, fraction=0.04, pad=0.02)
    return _done(plt, fig, own, path, title)


TARGETS_NAMED_TWICE = 40  # targets up to which target_panel names its columns as well as its rows
NUDGES = [(0, 0), (0, 6), (0, -6), (9, 0), (-9, 0), (8, 6), (-8, -6), (8, -6), (-8, 6), (0, 12), (0, -12),
          (16, 0), (-16, 0)]  # offsets in points tried in turn for a label on a map


def embedding_states(xy, states, names, path, title, named_on_map=8, fig=None):
    """The cells on a 2-D embedding (UMAP) coloured by named state, one colour per state (state_colours). Up to
    named_on_map states carry their names at their centres; with more, the map carries each state's ID and a legend
    beside it the full names."""
    plt = figures._plt()
    states = np.asarray(states).astype(str)
    col = state_colours(states)
    many = len(col) > named_on_map
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * (0.68 if many else 1.0)))
    ax = fig.add_subplot()
    order = np.random.default_rng(0).permutation(len(states))
    ax.scatter(xy[order, 0], xy[order, 1], s=1, c=[col[s] for s in states[order]], linewidths=0, rasterized=True)
    if many:
        handles = [plt.Line2D([], [], marker="o", ls="", ms=3, color=col[s]) for s in sorted(col)]
        ax.legend(handles, [names.get(s, s) for s in sorted(col)], loc="upper left", bbox_to_anchor=(1.0, 1.0),
                  fontsize=5, handletextpad=0.2, borderaxespad=0.2, frameon=False)
        ax.set_aspect("equal", adjustable="datalim")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("UMAP 1")
    ax.set_ylabel("UMAP 2")
    if own:
        fig.tight_layout(pad=0.4)

    def place(in_layout):
        fig.canvas.draw()
        renderer, placed = fig.canvas.get_renderer(), []
        for s in sorted(col):  # each name at its state's centre, moved to the nearest free place if it would overlap
            m = states == s
            if not m.any():
                continue
            cx, cy = np.median(xy[m], axis=0)
            for dx, dy in NUDGES:
                a = ax.annotate(s if many else names.get(s, s), (cx, cy), xytext=(dx, dy), textcoords="offset points",
                                fontsize=5, ha="center", va="center",
                                bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.7})
                a.set_in_layout(in_layout)
                box = a.get_window_extent(renderer)
                if not any(box.overlaps(b) for b in placed) or (dx, dy) == NUDGES[-1]:
                    placed.append(box)
                    break
                a.remove()
    _after_layout(fig, place)
    return _finish(plt, fig, path, title) if own else []


def marker_dots(table, path, title, states_order=None, fig=None):
    """A marker dot plot (table: state, gene, fraction, mean_expression; each gene in every state): dot size the
    fraction of the state's cells expressing the gene, with a key, colour its mean scaled across states (Seurat's
    DotPlot)."""
    plt = figures._plt()
    t = table.rename(columns={"mean": "mean_expression"})  # a table of an earlier run
    states_order = states_order or sorted(t["state"].unique())
    genes = list(dict.fromkeys(t["gene"]))
    t["z"] = t.groupby("gene")["mean_expression"].transform(
        lambda v: (v - v.mean()) / (v.std(ddof=0) if v.std(ddof=0) > 0 else 1.0))
    area = 30  # a dot's area in pt² for a gene every cell of the state expresses
    size = (DOUBLE if len(genes) > 20 else SINGLE, max(_height(len(states_order), 4.5), 50 * MM))
    fig, own = _canvas(plt, fig, size, layout="constrained")  # it keeps the colour bar's label inside
    grid = fig.add_gridspec(2, 2, width_ratios=[1, 0.03 if len(genes) > 20 else 0.06], height_ratios=[1.2, 1])
    ax, cax, kax = fig.add_subplot(grid[:, 0]), fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[1, 1])
    kax.axis("off")
    xi, yi = {g: i for i, g in enumerate(genes)}, {s: i for i, s in enumerate(states_order)}
    sc = ax.scatter([xi[g] for g in t["gene"]], [yi[s] for s in t["state"]], s=t["fraction"] * area, c=t["z"],
                    cmap="Reds", linewidths=0)
    ax.set_xticks(range(len(genes)))
    ax.set_xticklabels(genes, rotation=90, fontsize=5 if len(genes) > 30 else 6)
    ax.set_yticks(range(len(states_order)))
    ax.set_yticklabels(states_order)
    ax.set_xlim(-0.7, len(genes) - 0.3)
    ax.set_ylim(-0.7, len(states_order) - 0.3)
    cb = fig.colorbar(sc, cax=cax)
    cb.set_label("Mean expression,\nscaled", fontsize=6)  # two lines: it runs along a short colour bar
    cb.ax.tick_params(labelsize=5)
    ks = (0.25, 0.5, 0.75, 1.0)
    kax.legend([plt.Line2D([], [], ls="", marker="o", markersize=float(np.sqrt(k * area)), color="0.5") for k in ks],
               [f"{int(k * 100)}%" for k in ks], title="Cells expressing", title_fontsize=5, fontsize=5,
               loc="center left", frameon=False, handletextpad=0.2, labelspacing=0.6, borderaxespad=0.0)
    return _done(plt, fig, own, path, title)


PAIR_COLOURS = {"same target": "#D55E00", "different targets": "0.35", "control guides": "#0072B2"}


def pair_correlations(pairs, path, title, fig=None):
    """Two guides' profile correlations by kind of pair (pairs: kind, correlation): a box per kind with every pair
    drawn as a point, and the number of pairs in its label, so a reference of few pairs shows as few (as Akana et al.
    2026 and Belk et al. 2022 compare guides of one gene with other pairs)."""
    plt = figures._plt()
    kinds = [k for k in ("control guides", "different targets", "same target") if (pairs["kind"] == k).any()]
    fig, own = _canvas(plt, fig, (SINGLE, max(_height(len(kinds), 9, low=35), 35 * MM)))
    ax = fig.add_subplot()
    rng = np.random.default_rng(0)
    for i, kind in enumerate(kinds):
        v = pairs.loc[pairs["kind"] == kind, "correlation"].dropna().to_numpy()
        ax.boxplot([v], positions=[i], vert=False, widths=0.55, showfliers=False, medianprops={"color": "black"},
                   boxprops={"color": "0.4"}, whiskerprops={"color": "0.4"}, capprops={"color": "0.4"})
        ax.scatter(v, i + rng.uniform(-0.18, 0.18, len(v)), s=3 if len(v) > 200 else 6,
                   color=PAIR_COLOURS[kind], linewidths=0, alpha=0.6 if len(v) > 200 else 0.9, zorder=3)
    ax.axvline(0, color="0.6", lw=0.5, ls="--")
    ax.set_yticks(range(len(kinds)))
    ax.set_yticklabels([f"{k} ({int((pairs['kind'] == k).sum())})" for k in kinds])
    ax.set_ylim(-0.6, len(kinds) - 0.4)
    ax.set_xlim(-1, 1)
    ax.set_xlabel("Correlation of two guides' profiles")
    return _done(plt, fig, own, path, title)


def two_scores(table, x, y, path, title, xlabel, ylabel, label_top=8, group=None, group_order=None, log_y=False,
               diagonal=False, fig=None):
    """Each target's two scores (table: target, x, y), the control cells at zero (dashed lines), the label_top
    targets farthest from zero (each axis scaled to its largest value) named; group: a column whose values colour the
    points (group_colours, in group_order if given), keyed above the plot. log_y: a log scale up (for positive
    values such as a distance); diagonal: the line y = x (observed against expected) instead of the zero lines."""
    plt = figures._plt()
    t = table.dropna(subset=[x, y]).copy()
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * 0.85))
    ax = fig.add_subplot()
    size, raster = (8, False) if len(t) <= 300 else (2, True)  # many points (genes) drawn small
    if group:
        colours = group_colours(t[group], group_order)
        for g, c in colours.items():
            s = t[t[group].astype(str) == g]
            ax.scatter(s[x], s[y], s=size, color=c, linewidths=0, zorder=3, rasterized=raster)
        _key_above(ax, colours)
    else:
        ax.scatter(t[x], t[y], s=size, color="0.25", linewidths=0, zorder=3, rasterized=raster)
    if diagonal:
        lo, hi = float(min(t[x].min(), t[y].min())), float(max(t[x].max(), t[y].max()))
        ax.plot([lo, hi], [lo, hi], color="0.6", lw=0.5, ls="--", zorder=1)
    else:
        if not log_y:
            ax.axhline(0, color="0.6", lw=0.5, ls="--")
        ax.axvline(0, color="0.6", lw=0.5, ls="--")
    if log_y:
        ax.set_yscale("log")
        _plain_log(ax.yaxis)
    yv = np.log10(t[y].clip(lower=1e-12)) - np.log10(t[y].clip(lower=1e-12)).min() if log_y else t[y]
    t["far"] = np.hypot(t[x] / (t[x].abs().max() or 1.0), yv / (np.abs(yv).max() or 1.0))
    top = t.sort_values("far", ascending=False).head(label_top)
    _labels(ax, fig, zip(top[x], top[y], top["target"]))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return _done(plt, fig, own, path, title)


def density_difference(xy, target_mask, control_mask, path, title, label, bins=60, fig=None):
    """A target's cell density on the embedding minus the control cells' (both normalised), on a diverging scale."""
    plt = figures._plt()
    lo, hi = xy.min(0), xy.max(0)
    edges = [np.linspace(lo[k], hi[k], bins + 1) for k in (0, 1)]
    a = np.histogram2d(xy[target_mask, 0], xy[target_mask, 1], bins=edges)[0]
    b = np.histogram2d(xy[control_mask, 0], xy[control_mask, 1], bins=edges)[0]
    from scipy.ndimage import gaussian_filter
    d = gaussian_filter(a / max(a.sum(), 1), 1.5) - gaussian_filter(b / max(b.sum(), 1), 1.5)
    lim = float(np.abs(d).max()) or 1.0
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * 0.8))
    ax = fig.add_subplot()
    im = ax.imshow(d.T, origin="lower", extent=(lo[0], hi[0], lo[1], hi[1]), cmap="RdBu_r", vmin=-lim, vmax=lim,
                   aspect="auto")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("UMAP 1")
    ax.set_ylabel("UMAP 2")
    cb = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.02)
    cb.set_label(label, fontsize=6)
    cb.ax.tick_params(labelsize=5)
    return _done(plt, fig, own, path, title)


def signature_by_state(table, path, title, label, fig=None):
    """A signature's change within each state (table: target, state, change, and q if given): a heatmap of targets
    by states, clustered on targets, with significance marks."""
    M = table.pivot(index="target", columns="state", values="change")
    marks = None
    if "q" in table:
        Q = table.pivot(index="target", columns="state", values="q").reindex_like(M)
        marks = np.vectorize(lambda v: stars(v) if np.isfinite(v) else "")(Q.to_numpy(float))
    return heatmap(M.to_numpy(float), list(M.index), list(M.columns), path, title, label, marks=marks, fig=fig)


def enrichment_dots(table, list_size, path, title, q="q", top=20, fig=None):
    """Enrichment of one gene list or of several (up and down) as the field draws it (clusterProfiler's dotplot and
    compareCluster; table: set, overlap, q, and group for several lists; list_size: the list's genes, or {group:
    genes}). One list: the gene ratio (the list's genes in the set over the list's genes) along the axis, dot size the
    genes in the set. Several: one column per list, dot size the gene ratio. Colour -log10 adjusted p."""
    plt = figures._plt()
    t = table.copy()
    t[q] = pd.to_numeric(t[q], errors="coerce")
    t = t.dropna(subset=[q])
    if "group" not in t:
        t["group"] = "genes"
    sizes_of = list_size if isinstance(list_size, dict) else {g: list_size for g in t["group"].unique()}
    # a list with no hit gives an empty table, and concatenated with the others it makes the columns objects; the
    # sizes must be floats for matplotlib (development session 3, problem 22)
    t["overlap"] = pd.to_numeric(t["overlap"], errors="coerce").fillna(0).astype(float)
    t["ratio"] = (t["overlap"] / pd.to_numeric(t["group"].map(sizes_of), errors="coerce").clip(lower=1)).astype(float)
    groups = list(dict.fromkeys(t["group"]))
    keep = t.groupby("set")[q].min().sort_values().head(top).index
    t = t[t["set"].isin(keep)]
    one = len(groups) == 1
    sets = list(t.sort_values("ratio")["set"].drop_duplicates()) if one else list(keep)[::-1]
    yi = {s: i for i, s in enumerate(sets)}
    fig, own = _canvas(plt, fig, (SINGLE, _height(len(sets), 3.4, low=50)))
    grid = fig.add_gridspec(2, 2, width_ratios=[1, 0.05], height_ratios=[1.2, 1])  # the key below the colour bar
    ax, cax, kax = fig.add_subplot(grid[:, 0]), fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[1, 1])
    kax.axis("off")
    colour = -np.log10(t[q].clip(lower=1e-300))
    if one:
        size = 4 + 40 * np.sqrt(t["overlap"] / max(t["overlap"].max(), 1))
        sc = ax.scatter(t["ratio"], [yi[s] for s in t["set"]], s=size, c=colour, cmap="Reds", vmin=0,
                        linewidths=0.3, edgecolors="0.4")
        ax.set_xlim(0, float(t["ratio"].max()) * 1.15)
        from matplotlib.ticker import MaxNLocator
        ax.xaxis.set_major_locator(MaxNLocator(4))  # the plot is narrow beside its colour bar and key
        ax.set_xlabel("Gene ratio")
        ks = sorted({int(v) for v in np.quantile(t["overlap"], [0.0, 0.5, 1.0])})
        key_size, key_title = (lambda k: 4 + 40 * np.sqrt(k / max(t["overlap"].max(), 1))), "Genes"
    else:
        xi = {g: i for i, g in enumerate(groups)}
        top_ratio = max(float(t["ratio"].max()), 1e-9)
        sc = ax.scatter([xi[g] for g in t["group"]], [yi[s] for s in t["set"]], s=4 + 40 * np.sqrt(t["ratio"] /
                        top_ratio), c=colour, cmap="Reds", vmin=0, linewidths=0.3, edgecolors="0.4")
        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels([f"{g} ({sizes_of.get(g, '')})" for g in groups], fontsize=6, rotation=90)
        ax.set_xlim(-0.7, len(groups) - 0.3)
        from matplotlib.ticker import MaxNLocator
        ks = [float(v) for v in MaxNLocator(3).tick_values(0, top_ratio) if 0 < v <= top_ratio * 1.0001] \
            or [round(top_ratio, 2)]
        key_size, key_title = (lambda k: 4 + 40 * np.sqrt(k / top_ratio)), "Gene ratio"
    ax.set_yticks(range(len(sets)))
    ax.set_yticklabels([figures.short_name(s) for s in sets], fontsize=5 if len(sets) > 25 else 6)
    ax.set_ylim(-0.7, len(sets) - 0.3)
    cb = fig.colorbar(sc, cax=cax)
    cb.set_label("-log10 adjusted p", fontsize=6)
    cb.ax.tick_params(labelsize=5)
    kax.legend([plt.Line2D([], [], ls="", marker="o", markersize=float(np.sqrt(key_size(k))), color="0.6")
                for k in ks], [str(k) for k in ks],
               title=key_title, title_fontsize=5, fontsize=5, loc="center left", frameon=False, handletextpad=0.2,
               labelspacing=0.6, borderaxespad=0.0)
    return _done(plt, fig, own, path, title)


def pathway_dots(table, path, title, score="score", q="q", top=20, fig=None):
    """A pathway dot plot (table: set, group, score, q): one row per gene set, one column per group (a finding, a
    target), dot size -log10 q, colour the score on a diverging scale."""
    plt = figures._plt()
    t = table.copy()
    t[q], t[score] = pd.to_numeric(t[q], errors="coerce"), pd.to_numeric(t[score], errors="coerce")
    t = t.dropna(subset=[q, score])
    keep = t.groupby("set")[q].min().sort_values().head(top).index
    t = t[t["set"].isin(keep)]
    sets, groups = list(keep)[::-1], list(dict.fromkeys(t["group"]))
    t["size"] = np.clip(-np.log10(t[q].clip(lower=1e-30)), 0, 10) * 6 + 2
    lim = float(np.abs(t[score]).max()) or 1.0
    fig, own = _canvas(plt, fig, (SINGLE if len(groups) < 6 else DOUBLE, _height(len(sets), 3.2)))
    ax = fig.add_subplot()
    xi, yi = {g: i for i, g in enumerate(groups)}, {s: i for i, s in enumerate(sets)}
    sc = ax.scatter([xi[g] for g in t["group"]], [yi[s] for s in t["set"]], s=t["size"], c=t[score], cmap="RdBu_r",
                    vmin=-lim, vmax=lim, linewidths=0)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups, rotation=90, fontsize=6)
    ax.set_yticks(range(len(sets)))
    ax.set_yticklabels([figures.short_name(s) for s in sets], fontsize=5 if len(sets) > 25 else 6)
    ax.set_xlim(-0.7, len(groups) - 0.3)
    cb = fig.colorbar(sc, ax=ax, fraction=0.05, pad=0.02)
    cb.set_label(f"{score.replace('_', ' ')}", fontsize=6)
    cb.ax.tick_params(labelsize=5)
    return _done(plt, fig, own, path, title)


def knockdown_vs_baseline(table, path, title, label_top=8, fig=None):
    """Each guide's knockdown of its target gene against the gene's baseline expression in the control cells (table:
    guide, baseline, log2fc, and past_the_line if given): a guide short of the line drawn hollow."""
    plt = figures._plt()
    t = table.dropna(subset=["baseline", "log2fc"])
    t = t[t["baseline"] > 0].copy()  # a log axis: a gene never seen in the control cells has no place on it
    works = t["past_the_line"].astype(bool).to_numpy() if "past_the_line" in t else np.ones(len(t), bool)
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * 0.8))
    ax = fig.add_subplot()
    ax.scatter(t["baseline"][works], t["log2fc"][works], s=7, color="0.2", linewidths=0, zorder=3)
    ax.scatter(t["baseline"][~works], t["log2fc"][~works], s=9, facecolors="none", edgecolors="0.55",
               linewidths=0.6, zorder=3)
    ax.axhline(0, color="0.6", lw=0.5, ls="--")
    if (~works).any():  # the key, above the plot: what the two marks mean
        key = [plt.Line2D([], [], marker="o", ls="", ms=2.6, color="0.2"),
               plt.Line2D([], [], marker="o", ls="", ms=3, markerfacecolor="none", markeredgecolor="0.55",
                          markeredgewidth=0.6)]
        ax.legend(key, ["Passes threshold", "Fails threshold"], loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2,
                  fontsize=5, frameon=False, handletextpad=0.4, columnspacing=0.8, borderaxespad=0.1)
    ax.set_xscale("log")
    _plain_log(ax.xaxis)  # plain numbers; minor labels crowd a log axis
    top = t.sort_values("log2fc").head(label_top)
    _labels(ax, fig, zip(top["baseline"], top["log2fc"], top["guide"]))
    ax.set_xlabel("Target gene in control cells, mean UMIs per cell")
    ax.set_ylabel("Target gene, log2 FC vs control cells")
    return _done(plt, fig, own, path, title)


def pseudotime_densities(pseudotime, groups, path, title, order=None, control="control", fig=None):
    """Each group's density of cells along pseudotime (descriptive; no fate inference), the control cells grey. Every
    group is smoothed with one width (Scott's rule on the cells drawn), so a group of fewer cells is not drawn
    flatter; the axis ends at the largest of the groups' 99.5th percentiles, so a few far cells do not squeeze the
    rest."""
    plt = figures._plt()
    from scipy.stats import gaussian_kde
    pt = np.asarray(pseudotime, float)
    g = np.asarray(groups).astype(str)
    order = order or sorted(set(g) - {control})
    fig, own = _canvas(plt, fig, (SINGLE, 50 * MM))
    ax = fig.add_subplot()
    cols = figures._colours(plt, len(order))
    drawn = [(name, colour, pt[(g == name) & np.isfinite(pt)])
             for name, colour in [(control, CONTROL)] + list(zip(order, cols))]
    drawn = [(name, colour, v) for name, colour, v in drawn if len(np.unique(v)) > 2]
    if drawn:
        every = np.concatenate([v for *_, v in drawn])
        width = every.std(ddof=1) * len(every) ** -0.2
        grid = np.linspace(np.nanmin(pt), max(np.percentile(v, 99.5) for *_, v in drawn), 200)
    for name, colour, v in drawn:
        ax.plot(grid, gaussian_kde(v, bw_method=width / v.std(ddof=1))(grid), color=colour,
                lw=0.9 if name != control else 1.2, label=name)
    ax.set_xlabel("Pseudotime")
    ax.set_ylabel("Density of cells")
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=5, handlelength=1.2)
    return _done(plt, fig, own, path, title)


def strips(table, value, group, path, xlabel, title="", order=None, log=False, fig=None, points_at_most=None,
           xlim=None):
    """A value's spread in each group (table: group, value), a box per group with every item a point, the number of
    items in the group's label: cells per guide for the targeting and the control guides, for example. With
    points_at_most, a larger group shows that many points drawn at random; its box holds every item. xlim: the
    axis's range where the value has one (a share, 0 to 1)."""
    plt = figures._plt()
    t = table.dropna(subset=[value])
    groups = order or list(dict.fromkeys(t[group]))
    fig, own = _canvas(plt, fig, (SINGLE, max(_height(len(groups), 9, low=35), 35 * MM)))
    ax = fig.add_subplot()
    rng = np.random.default_rng(0)
    for i, g in enumerate(groups):
        v = t.loc[t[group] == g, value].to_numpy(float)
        ax.boxplot([v], positions=[i], vert=False, widths=0.55, showfliers=False, medianprops={"color": "black"},
                   boxprops={"color": "0.4"}, whiskerprops={"color": "0.4"}, capprops={"color": "0.4"})
        shown = rng.choice(v, points_at_most, replace=False) if points_at_most and len(v) > points_at_most else v
        ax.scatter(shown, i + rng.uniform(-0.18, 0.18, len(shown)), s=3 if len(shown) > 200 else 6, color="0.3",
                   linewidths=0, alpha=0.7, zorder=3)
    if log:
        ax.set_xscale("log")
        _plain_log(ax.xaxis)
    if xlim:
        ax.set_xlim(*xlim)
    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels([f"{g} ({int((t[group] == g).sum())})" for g in groups])
    ax.set_ylim(-0.6, len(groups) - 0.4)
    ax.set_xlabel(xlabel)
    return _done(plt, fig, own, path, title)


UP, DOWN = "#B2182B", "#2166AC"  # the ends of the diverging scale the heatmaps use (RdBu_r)


def network(edges, path, title, top=60, fig=None):
    """Targets acting on one another (edges: source, target, effect): each target a point on a circle, an arrow from a
    perturbed target to the target whose target gene it changes, red where it raises the gene and blue where it lowers
    it, as wide as the change is large; the `top` largest changes."""
    plt = figures._plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyArrowPatch
    e = edges.loc[edges["effect"].abs().sort_values(ascending=False).index].head(top)
    nodes = sorted(set(e["source"].astype(str)) | set(e["target"].astype(str)))
    angles = np.linspace(0, 2 * np.pi, max(len(nodes), 1), endpoint=False) + np.pi / 2
    at = {v: (np.cos(a), np.sin(a)) for v, a in zip(nodes, angles)}
    side = SINGLE if len(nodes) <= 20 else DOUBLE
    fig, own = _canvas(plt, fig, (side, side))
    ax = fig.add_subplot()
    widest = float(e["effect"].abs().max()) if len(e) else 1.0
    for r in e.itertuples():
        ax.add_patch(FancyArrowPatch(at[str(r.source)], at[str(r.target)], arrowstyle="-|>", mutation_scale=6,
                                     color=UP if r.effect > 0 else DOWN, lw=0.4 + 1.6 * abs(r.effect) / widest,
                                     connectionstyle="arc3,rad=0.12", shrinkA=5, shrinkB=5, alpha=0.85))
    for v, (x, y) in at.items():
        ax.scatter([x], [y], s=14, color="0.25", zorder=3)
        ax.text(1.13 * x, 1.13 * y, v, ha="center", va="center", fontsize=5)
    ax.legend([Line2D([], [], color=UP), Line2D([], [], color=DOWN)], ["Gene up", "Gene down"],
              loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, fontsize=5, frameon=False)
    ax.set_xlim(-1.4, 1.4)
    ax.set_ylim(-1.4, 1.4)
    ax.set_aspect("equal")
    ax.axis("off")
    return _done(plt, fig, own, path, title)


GROUP_COLOURS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000"]


def group_colours(groups, order=None):
    """One colour per group (a mode, a condition), fixed by the groups' sorted names (or the order given), so every
    chart of a session shows a group alike."""
    names = [str(g) for g in order] if order else sorted({str(g) for g in groups})
    return {g: GROUP_COLOURS[i % len(GROUP_COLOURS)] for i, g in enumerate(names)}


def _key_above(ax, colours, marker="o"):
    """A one-row key of the groups' colours above the plot, outside its data area."""
    import matplotlib.pyplot as plt
    _key(ax, [plt.Line2D([], [], marker=marker, ls="", ms=3, color=c) for c in colours.values()], list(colours))


def _key(ax, handles, names):
    """A key above the plot, outside its data area: what each colour, mark or band stands for, in rows of three."""
    ax.legend(list(handles), list(names), loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=min(len(names), 3),
              fontsize=5, frameon=False, handletextpad=0.4, columnspacing=0.8, borderaxespad=0.1)


def design_panel(stages, path=None, title="The screen's design", fig=None):
    """The screen's design drawn from its confirmed facts: one box per stage (stages: [(heading, [lines])], such as
    the library, the cells, the samples, the readout), left to right with arrows between them."""
    import textwrap
    from matplotlib.patches import FancyBboxPatch
    plt = figures._plt()
    fig, own = _canvas(plt, fig, (DOUBLE, 28 * MM))
    ax = fig.add_subplot()
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    n = len(stages)
    ax.set_xlim(0, n)
    ax.set_ylim(0, 1)
    for i, (heading, lines) in enumerate(stages):
        ax.add_patch(FancyBboxPatch((i + 0.08, 0.04), 0.84, 0.92, boxstyle="round,pad=0,rounding_size=0.04",
                                    facecolor="0.96", edgecolor="0.55", linewidth=0.6))
        ax.text(i + 0.5, 0.88, heading, ha="center", va="top", fontsize=7, fontweight="bold")
        body = "\n".join(w for line in lines for w in textwrap.wrap(str(line), 34, break_on_hyphens=False))
        ax.text(i + 0.5, 0.66, body, ha="center", va="top", fontsize=6, linespacing=1.35)
        if i < n - 1:
            ax.annotate("", xy=(i + 1.1, 0.5), xytext=(i + 0.9, 0.5),
                        arrowprops={"arrowstyle": "-|>", "color": "0.35", "lw": 0.8, "shrinkA": 0, "shrinkB": 0})
    return _done(plt, fig, own, path, title)


def target_panel(C, side, path=None, title="The targets", label="Correlation of profiles", fig=None, order=None,
                 boxes=()):
    """The targets in one panel, as Belk et al. 2022 draw them: the correlation of every two targets' profiles (C,
    a square table; missing values grey), clustered, with a column of bars beside it for each measure in side (a
    table indexed by target: cells, genes changed, a score), signed measures red above zero and blue below. order:
    the targets' order, where a clustering of its own gives it; boxes: sets of targets outlined on the diagonal, each
    a run of neighbours in that order."""
    import textwrap
    from matplotlib.patches import Rectangle
    plt = figures._plt()
    C = C.astype(float)
    names = list(C.index)
    if order is None:
        r, _ = _order(C.to_numpy())
        order = [names[i] for i in r]
    else:
        order = [x for x in order if x in C.index] + [x for x in names if x not in set(order)]
    M = C.loc[order, order].to_numpy()
    k = side.shape[1]
    n = len(order)
    fig, own = _canvas(plt, fig, (DOUBLE, _height(n, 2.4, low=60) + 14 * MM), layout="constrained")
    grid = fig.add_gridspec(1, k + 2, width_ratios=[max(n, 8)] + [max(n, 8) * 0.13] * k + [max(n, 8) * 0.04],
                            wspace=0.08)
    ax = fig.add_subplot(grid[0, 0])
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad("0.85")
    lim = float(np.nanmax(np.abs(M[~np.eye(n, dtype=bool)]))) if n > 1 and np.isfinite(M).any() else 1.0
    im = ax.imshow(np.ma.masked_invalid(M), cmap=cmap, vmin=-lim, vmax=lim, aspect="auto", interpolation="nearest")
    at = {x: i for i, x in enumerate(order)}
    for members in boxes:
        rows = sorted(at[x] for x in members if x in at)
        if len(rows) > 1 and rows[-1] - rows[0] + 1 == len(rows):  # a box outlines neighbours only
            ax.add_patch(Rectangle((rows[0] - 0.5, rows[0] - 0.5), len(rows), len(rows), fill=False, lw=0.8,
                                   edgecolor="black"))
    ax.set_yticks(range(n))
    ax.set_yticklabels(order, fontsize=5 if n > 30 else 6)
    ax.set_xticks(range(n))
    if n <= TARGETS_NAMED_TWICE:
        ax.set_xticklabels(order, rotation=90, fontsize=5 if n > 30 else 6)
    else:  # the matrix is symmetric: the rows name the targets, the columns' names would overlap
        ax.tick_params(axis="x", labelbottom=False)
    # a measure's name runs across above its bars, wrapped to their width (about 1.35 mm a character at 6 pt):
    # below the bars it reached the next panel, and turned upright above them it took the rows' height
    wrap = max(6, int(fig.bbox.width / fig.dpi * 25.4 * 0.13 / (1.04 + 0.13 * k) / 1.35))
    for j, col in enumerate(side.columns):
        bx = fig.add_subplot(grid[0, j + 1], sharey=ax)
        v = pd.to_numeric(side[col].reindex(order), errors="coerce").to_numpy(float)
        signed = np.nanmin(v) < 0 if np.isfinite(v).any() else False
        colour = [("#B2182B" if x > 0 else "#2166AC") if signed else "0.35" for x in np.nan_to_num(v)]
        bx.barh(range(n), np.nan_to_num(v), height=0.75, color=colour, linewidth=0)
        if signed:
            bx.axvline(0, color="0.5", lw=0.4)
        bx.tick_params(axis="y", left=False, labelleft=False)
        bx.xaxis.set_ticks_position("top")
        from matplotlib.ticker import MaxNLocator
        bx.xaxis.set_major_locator(MaxNLocator(2, symmetric=bool(signed)))
        bx.tick_params(axis="x", labelsize=5, rotation=90, pad=1)
        bx.spines["top"].set_visible(True)
        bx.spines["left"].set_visible(False)
        bx.spines["bottom"].set_visible(False)
        bx.xaxis.set_label_position("top")
        bx.set_xlabel(textwrap.fill(str(col), wrap), fontsize=6, ha="center", va="bottom", linespacing=1.0)
    ax.set_ylim(n - 0.5, -0.5)
    diverging_colour_bar(fig, im, label, cax=fig.add_subplot(grid[0, k + 1]))
    return _done(plt, fig, own, path, title)


def embedding_highlight(xy, groups, highlight, path=None, title="Cells on the embedding", states=None, fig=None):
    """All cells on the embedding in light grey, the cells of each highlighted group (highlight: groups, in drawing
    order) on top in its colour (group_colours), keyed above the plot; with states, each state's ID at its centre,
    to match the states' embedding."""
    plt = figures._plt()
    groups = np.asarray(groups).astype(str)
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * 0.8))
    ax = fig.add_subplot()
    ax.scatter(xy[:, 0], xy[:, 1], s=0.6, color="0.86", linewidths=0, rasterized=True)
    colours = group_colours(highlight)
    for g in highlight:
        m = groups == g
        ax.scatter(xy[m, 0], xy[m, 1], s=1.6, color=colours[str(g)], linewidths=0, rasterized=True)
    if states is not None:
        states = np.asarray(states).astype(str)
        for s in sorted(set(states)):
            cx, cy = np.median(xy[states == s], axis=0)
            ax.annotate(s, (cx, cy), fontsize=5, ha="center", va="center",
                        bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.6})
    _key_above(ax, {str(g): colours[str(g)] for g in highlight})
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("UMAP 1")
    ax.set_ylabel("UMAP 2")
    return _done(plt, fig, own, path, title)


def embedding_values(xy, values, path, label, title="", log=False, fig=None):
    """The cells on a 2-D embedding (UMAP) coloured by one measure per cell (a quality measure such as the
    mitochondrial share), on a sequential scale with its colour bar; log: the measure's log10."""
    plt = figures._plt()
    v = np.asarray(values, float)
    if log:
        v = np.log10(np.clip(v, 1e-12, None))
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * 0.85))
    ax = fig.add_subplot()
    order = np.argsort(np.nan_to_num(v, nan=-np.inf))  # the highest values drawn last, on top
    sc = ax.scatter(xy[order, 0], xy[order, 1], s=0.8, c=v[order], cmap="viridis", linewidths=0, rasterized=True)
    cb = fig.colorbar(sc, ax=ax, fraction=0.05, pad=0.02)
    cb.set_label(label + (" (log10)" if log else ""), fontsize=6)
    cb.ax.tick_params(labelsize=5)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("UMAP 1")
    ax.set_ylabel("UMAP 2")
    return _done(plt, fig, own, path, title)


def distributions(table, value, group, path, xlabel, title="", log=False, cuts=None, bins=60, key=True, fig=None):
    """A measure's distribution in each group (table: group, value), one step histogram per group on shared bins
    (group_colours), keyed above the plot (beside it for more than four groups; key False for none, as on all but
    one of a row of them): the cells' quality measures per sample, for example. log: bins on a log10 scale; cuts:
    values drawn as dashed vertical lines (a proposed threshold)."""
    plt = figures._plt()
    t = table.dropna(subset=[value])
    v = t[value].to_numpy(float)
    if log:
        v = np.log10(np.clip(v, 1e-12, None))
    groups = list(dict.fromkeys(t[group].astype(str)))
    colours = group_colours(groups) if len(groups) <= len(GROUP_COLOURS) else state_colours(groups)
    fig, own = _canvas(plt, fig, (SINGLE, SINGLE * 0.7))
    ax = fig.add_subplot()
    edges = np.histogram_bin_edges(v[np.isfinite(v)], bins=bins) if np.isfinite(v).any() else bins
    labels = t[group].astype(str).to_numpy()
    for g in groups:
        ax.hist(v[labels == g], bins=edges, histtype="step", color=colours[g], lw=0.7)
    for c in cuts or ():
        ax.axvline(np.log10(c) if log else c, color="black", lw=0.6, ls="--", zorder=3)
    if key and 1 < len(groups) <= 4:
        _key_above(ax, colours, marker="s")
    elif key and len(groups) > 4:  # many groups: a key beside the plot, one per line
        handles = [plt.Line2D([], [], color=colours[g], lw=0.9) for g in groups]
        ax.legend(handles, groups, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=5, frameon=False,
                  handlelength=1.0, handletextpad=0.3, borderaxespad=0.1)
    ax.set_xlabel(xlabel + (" (log10)" if log else ""))
    ax.set_ylabel("Cells")
    return _done(plt, fig, own, path, title)
