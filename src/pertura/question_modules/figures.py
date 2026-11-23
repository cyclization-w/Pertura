"""The modules' figures, in one style (docs/adr/0020, section 6), following Nature's figure guidelines: Arial at
6-7 pt, single- or double-column widths, open axes, and nothing drawn that the axes do not need (no titles, no notes
in the plot). Samples may be drawn as dots (not a rule: docs/adr/0022, section 4), the reference as a thin dashed line, colour scales are centred on zero, and the
axis label says what a value is against, which gives its direction. A figure's title is kept in the PNG's metadata.
Each drawing function returns the checks it met, which the module reports with its figure, the layout checks' issues
among them (check_figure, run on every figure these functions save)."""
import re

import numpy as np

MM = 1 / 25.4
SINGLE, DOUBLE = 89 * MM, 183 * MM  # column widths, inches
HEIGHT = 58 * MM
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
STYLE = {"font.family": "sans-serif", "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
         "font.size": 7, "axes.labelsize": 7, "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 6,
         "legend.title_fontsize": 6, "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
         "xtick.major.size": 2.5, "ytick.major.size": 2.5, "axes.spines.top": False, "axes.spines.right": False,
         "legend.frameon": False, "lines.linewidth": 0.75, "savefig.dpi": 300, "figure.dpi": 300,
         "pdf.fonttype": 42,
         "svg.fonttype": "none"}
# an axis label that names a comparison, so it gives the direction
COMPARED = re.compile(r"\bvs\b|−|\bminus\b|\bagainst\b|\bratio\b|\bcorrelation\b", re.I)


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update(STYLE)
    return plt


def _colours(plt, n):
    return OKABE_ITO[:n] if n <= len(OKABE_ITO) else [plt.get_cmap("tab20")(k % 20) for k in range(n)]


def _width(n_groups):
    """One column while the groups fit in it, else two: a figure is 89 or 183 mm wide, never between."""
    return SINGLE if 0.11 * n_groups + 0.9 <= SINGLE else DOUBLE


def _overlap(a, b):
    """Two text boxes (display coordinates) overlapping by more than a pixel each way."""
    if not a.overlaps(b):
        return False
    both = a.intersection(a, b)
    return both is not None and both.width > 1 and both.height > 1


def _upright_where_crowded(fig):
    """Names under an axis set at 45 degrees that overlap their neighbours are set upright (90 degrees), where each
    takes only its height across the axis, and the figure is laid out again. A rotated name's box is the rectangle
    around it, so long names a few columns apart overlap at 45 degrees."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    moved = False
    for ax in fig.axes:
        names = [t for t in ax.get_xticklabels() if t.get_visible() and t.get_text().strip()]
        if len(names) < 2 or any(abs(t.get_rotation() - 45) > 1e-6 for t in names):
            continue
        boxes = [t.get_window_extent(renderer) for t in names]
        if any(_overlap(a, b) for a, b in zip(boxes, boxes[1:])):
            for t in names:
                t.set_rotation(90)
                t.set_horizontalalignment("center")
            moved = True
    if moved:
        fig.tight_layout(pad=0.4)


def _save(plt, fig, path, title):
    """Lay the figure out, set crowded names upright (_upright_where_crowded), check it (check_figure) and save it;
    returns the issues. The file is cropped to what is drawn, so a long label is never clipped: text past the
    figure's edge is kept and makes the file larger than its set width, which the issue says."""
    fig.tight_layout(pad=0.4)
    _upright_where_crowded(fig)
    issues = [i.replace("is cut at the figure's edge", "runs past the figure's edge (the file is larger than its "
                                                        "set width)") for i in check_figure(fig)]
    fig.savefig(path, metadata={"Title": title}, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    return issues


def _dots_on(plt, ax, table, x, y, unit, groups, reference, threshold=None):
    """Dots per unit in columns `groups`, a short line at each column's mean where it holds two or more dots, the
    reference as a dashed line; a legend only when there are two or more units."""
    pos = {g: i for i, g in enumerate(groups)}
    units = sorted(table[unit].astype(str).unique())
    colours = _colours(plt, len(units))
    step = min(0.08, 0.6 / max(len(units), 1))
    for k, u in enumerate(units):
        s = table[table[unit].astype(str) == u]
        jitter = (k - (len(units) - 1) / 2) * step
        ax.scatter([pos[g] + jitter for g in s[x]], s[y], s=7, color=colours[k], label=str(u), linewidths=0,
                   zorder=3)
    means = table.groupby(x)[y].mean()[table.groupby(x)[y].count() >= 2]
    ax.hlines(means.values, [pos[g] - 0.3 for g in means.index], [pos[g] + 0.3 for g in means.index],
              color="black", lw=0.75, zorder=4)
    if reference is not None:
        ax.axhline(reference, color="0.45", lw=0.5, ls="--", zorder=1)
    if threshold is not None:
        ax.axhline(threshold, color="0.6", lw=0.5, ls=":", zorder=1)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups, rotation=90 if len(groups) > 12 else 45, ha="center" if len(groups) > 12 else "right",
                       fontsize=5 if len(groups) > 40 else 6)
    ax.set_xlim(-0.7, len(groups) - 0.3)
    lo, hi = ax.get_ylim()  # room on both sides of the reference line
    span = hi - lo
    if reference is not None:
        ax.set_ylim(min(lo, reference - 0.08 * span), max(hi, reference + 0.08 * span))
    if 2 <= len(units) <= 12:
        ax.legend(title="Unit", loc="upper left", bbox_to_anchor=(1.0, 1.0), handletextpad=0.2, borderaxespad=0.2,
                  markerscale=1.2)
    return units


def dots(table, x, y, unit, path, title, ylabel, reference=0.0, max_groups=40, order=None, threshold=None):
    """One column per group (x), one dot per unit, a short line at the mean of the units; a dashed line at the
    reference value (none when reference is None: the value compares nothing), a dotted one at a threshold."""
    plt = _plt()
    t = table.copy()
    groups = order or list(t.groupby(x)[y].mean().sort_values().index)
    n_all = t[x].nunique()
    groups = groups[:max_groups]
    t = t[t[x].isin(groups)]
    fig, ax = plt.subplots(figsize=(_width(len(groups)), HEIGHT))
    _dots_on(plt, ax, t, x, y, unit, groups, reference, threshold)
    ax.set_ylabel(ylabel)
    if n_all > len(groups):  # said on the axis, never cut silently
        ax.set_xlabel(f"{len(groups)} of {n_all} shown, " + ("in the order given" if order else "lowest means"))
    issues = _save(plt, fig, path, title)
    return {"reference_shown": reference is not None or None, "zero_centred": None,
            "direction_labelled": bool(COMPARED.search(ylabel)) if reference is not None else None,
            "layout_issues": issues}


def short_name(name):
    """A gene set's name for an axis: the hallmark collection's prefix dropped, underscores as spaces."""
    name = str(name)
    return (name[len("HALLMARK_"):] if name.startswith("HALLMARK_") else name).replace("_", " ")


def _upright(M, rows, cols):
    """The longer list on the rows, where its names read across; the map is the same either way."""
    if len(cols) > len(rows):
        return np.asarray(M).T, list(cols), list(rows)
    return M, list(rows), list(cols)


def _map_height(rows, cols):
    """Inches for a heatmap of these rows and its column names below it, so neither is squeezed."""
    return 0.11 * len(rows) + 0.055 * max([len(str(c)) for c in cols] or [1]) + 0.4


def _heatmap_on(plt, fig, ax, M, rows, cols, label):
    from matplotlib.colors import TwoSlopeNorm
    lim = float(np.nanmax(np.abs(M))) if np.isfinite(M).any() else 1.0
    lim = lim if lim > 0 else 1.0
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad("0.85")  # a missing value is grey, apart from zero's white
    im = ax.imshow(np.ma.masked_invalid(M), aspect="auto", cmap=cmap, norm=TwoSlopeNorm(vcenter=0.0, vmin=-lim, vmax=lim),
                   interpolation="nearest")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=5 if len(rows) > 30 else 6)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=90, fontsize=5 if len(cols) > 30 else 6)
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.01)
    cb.set_label(label)
    cb.outline.set_linewidth(0.5)
    cb.ax.tick_params(width=0.5, length=2)
    return cb.ax


def _save_two_parts(plt, fig, path, title, parts):
    """A two-part figure: each part saved alone first (<figure>_part_a.png, _part_b.png), for a panel that letters
    its own tiles, then the whole with its letters a and b. parts: [(the axis lettered, [its axes])]."""
    from matplotlib.transforms import Bbox
    fig.tight_layout(pad=0.4)
    _upright_where_crowded(fig)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    stem = path[:-4] if path.endswith(".png") else path
    every = [a for _, group in parts for a in group]
    boxes = [Bbox.union([a.get_tightbbox(renderer) for a in axes]).transformed(fig.dpi_scale_trans.inverted())
             for _, axes in parts]
    for letter, (_, axes), box in zip("ab", parts, boxes):
        for a in every:  # the other part's labels can reach into this part's frame
            a.set_visible(a in axes)
        fig.savefig(f"{stem}_part_{letter}.png", metadata={"Title": title}, bbox_inches=box.padded(0.08))
    for a in every:
        a.set_visible(True)
    for letter, (a, _) in zip("ab", parts):
        a.text(-0.02, 1.02, letter, transform=a.transAxes, fontsize=8, fontweight="bold", ha="right", va="bottom")
    return _save(plt, fig, path, title)


def heatmap(matrix, rows, cols, path, title, label):
    """A diverging heatmap centred on zero (the reference), rows x cols; the colour bar's label says what a value is
    against."""
    plt = _plt()
    M, rows, cols = _upright(np.asarray(matrix, float), rows, cols)
    fig, ax = plt.subplots(figsize=(_width(len(cols)), max(HEIGHT, _map_height(rows, cols))))
    _heatmap_on(plt, fig, ax, M, rows, cols, label)
    issues = _save(plt, fig, path, title)
    return {"reference_shown": True, "zero_centred": True,
            "direction_labelled": bool(COMPARED.search(label)), "layout_issues": issues}


def heatmap_and_dots(matrix, rows, cols, table, x, y, unit, path, title, label):
    """a, a diverging heatmap centred on zero (rows x cols, the mean over units); b, the largest pairs in `table`
    (column x names the pair, y its value), one dot per unit, with the reference at zero."""
    plt = _plt()
    M, rows, cols = _upright(np.asarray(matrix, float), rows, cols)
    groups = list(table.groupby(x)[y].mean().sort_values().index)
    top_h = max(_map_height(rows, cols), 1.2)
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(_width(max(len(cols), len(groups))), top_h + HEIGHT + 0.6),
                                 gridspec_kw={"height_ratios": [top_h, HEIGHT + 0.6]})
    cbax = _heatmap_on(plt, fig, ax, M, rows, cols, label)
    _dots_on(plt, bx, table, x, y, unit, groups, 0.0)
    bx.set_ylabel(label)
    issues = _save_two_parts(plt, fig, path, title, [(ax, [ax, cbax]), (bx, [bx])])
    return {"reference_shown": True, "zero_centred": True,
            "direction_labelled": bool(COMPARED.search(label)), "layout_issues": issues}


def condition_map(matrix, rows, cols, table, x, y, condition, conditions, path, title, label, ylabel, shown=None):
    """a, a diverging heatmap centred on zero of how each effect changes between two conditions (rows x cols), with
    `shown` saying on its axis what part is drawn; b, the largest of them in the order given (column x names each),
    one dot per unit, the two conditions side by side, a short line at each condition's mean, the reference at
    zero."""
    plt = _plt()
    M = np.asarray(matrix, float)
    groups = list(dict.fromkeys(table[x]))
    top_h = max(0.09 * len(rows) + 1.0, 1.2)
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(_width(max(len(cols), 2 * len(groups))), top_h + HEIGHT + 0.6),
                                 gridspec_kw={"height_ratios": [top_h, HEIGHT + 0.6]})
    cbax = _heatmap_on(plt, fig, ax, M, rows, cols, label)
    if shown:
        ax.set_xlabel(shown)
    pos = {g: i for i, g in enumerate(groups)}
    colours = _colours(plt, len(conditions))
    for k, c in enumerate(conditions):
        s = table[table[condition].astype(str) == str(c)]
        off = (k - (len(conditions) - 1) / 2) * 0.3
        bx.scatter([pos[g] + off for g in s[x]], s[y], s=7, color=colours[k], label=str(c), linewidths=0, zorder=3)
        m = s.groupby(x)[y].mean()
        bx.hlines(m.values, [pos[g] + off - 0.12 for g in m.index], [pos[g] + off + 0.12 for g in m.index],
                  color="black", lw=0.75, zorder=4)
    bx.axhline(0.0, color="0.45", lw=0.5, ls="--", zorder=1)
    bx.set_xticks(range(len(groups)))
    bx.set_xticklabels(groups, rotation=45, ha="right")
    bx.set_xlim(-0.7, len(groups) - 0.3)
    bx.set_ylabel(ylabel)
    bx.legend(title="Condition", loc="upper left", bbox_to_anchor=(1.0, 1.0), handletextpad=0.2, borderaxespad=0.2)
    issues = _save_two_parts(plt, fig, path, title, [(ax, [ax, cbax]), (bx, [bx])])
    return {"reference_shown": True, "zero_centred": True,
            "direction_labelled": bool(COMPARED.search(label)), "layout_issues": issues}


def bars(values, labels, path, title, ylabel, stacked=None, xlabel=None):
    """Counts per unit (stacked by kind when stacked is a list of column names of values, a DataFrame)."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(_width(len(labels)), HEIGHT))
    if stacked is not None:
        bottom = np.zeros(len(values))
        for col, colour in zip(stacked, _colours(plt, len(stacked))):
            ax.bar(range(len(values)), values[col].to_numpy(), bottom=bottom, label=col, color=colour, width=0.7)
            bottom = bottom + values[col].to_numpy()
        ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), handlelength=0.8, handletextpad=0.3)
    else:
        ax.bar(range(len(values)), values, color="0.4", width=0.7)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90 if len(labels) > 12 else 45, ha="center" if len(labels) > 12 else "right")
    ax.set_ylabel(ylabel)
    if xlabel:
        ax.set_xlabel(xlabel)
    issues = _save(plt, fig, path, title)
    return {"reference_shown": None, "zero_centred": None, "direction_labelled": None, "layout_issues": issues}


def cells_and_quality(values, labels, kinds, measures, captures, path, title):
    """The cells by kind per unit (stacked bars, as bars draws them) above each capture's quality measures (UMIs on a
    log axis, genes detected, mitochondrial share; boxes without outlier points, the cells being many)."""
    plt = _plt()
    caps = sorted(set(map(str, captures)))
    width = max(_width(len(labels)), _width(3 * len(caps)))
    fig = plt.figure(figsize=(width, HEIGHT * 2.1))
    grid = fig.add_gridspec(2, 3, height_ratios=[1, 1])
    ax = fig.add_subplot(grid[0, :])
    bottom = np.zeros(len(values))
    for col, colour in zip(kinds, _colours(plt, len(kinds))):
        ax.bar(range(len(values)), values[col].to_numpy(), bottom=bottom, label=col, color=colour, width=0.7)
        bottom = bottom + values[col].to_numpy()
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), handlelength=0.8, handletextpad=0.3)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90 if len(labels) > 12 else 45, ha="center" if len(labels) > 12 else "right")
    ax.set_ylabel("Cells")
    ax.set_xlabel("Unit")
    cap = np.asarray(captures).astype(str)
    for k, (col, name, log) in enumerate((("n_counts", "UMIs per cell", True), ("n_genes", "Genes per cell", False),
                                          ("pct_mito", "Mitochondrial share", False))):
        a = fig.add_subplot(grid[1, k])
        data = [measures[col].to_numpy()[cap == c] for c in caps]
        a.boxplot(data, showfliers=False, widths=0.6, medianprops={"color": "black"})
        if log:
            a.set_yscale("log")
        a.set_xticks(range(1, len(caps) + 1))
        a.set_xticklabels(caps, rotation=90, fontsize=5)
        a.set_ylabel(name)
    issues = _save(plt, fig, path, title)
    return {"reference_shown": None, "zero_centred": None, "direction_labelled": None, "layout_issues": issues}


# ---- checks before a figure enters the record (plugin/skills/pertura/figures.md) ----------------------------------
WIDTHS_MM = (89, 183)  # one and two columns
MIN_PT = 5


def check_figure(fig, min_pt=MIN_PT, widths_mm=WIDTHS_MM, tolerance_mm=1.0):
    """The mechanical faults of a finished figure: text below min_pt, text overlapping other text, text cut at the
    figure's edge, a width that is not one of widths_mm, and text that a PDF or SVG would not keep editable. Returns a
    list of issues (empty: none)."""
    import matplotlib
    from matplotlib.text import Text
    issues = []
    width = fig.get_size_inches()[0] / MM
    if not any(abs(width - w) <= tolerance_mm for w in widths_mm):
        issues.append(f"width {width:.0f} mm, not one of {', '.join(f'{w} mm' for w in widths_mm)}")
    if matplotlib.rcParams.get("pdf.fonttype") != 42 or matplotlib.rcParams.get("svg.fonttype") != "none":
        issues.append("text would not stay editable (pdf.fonttype 42, svg.fonttype none)")
    if not hasattr(fig.canvas, "get_renderer"):  # closed by pyplot, as the notebook closes each cell's figures
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    undrawn = set()  # tick labels beyond the axis' limits: kept by matplotlib, never drawn
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            shown = {id(x) for t in axis._update_ticks() for x in (t.label1, t.label2)}
            undrawn |= {id(x) for t in axis.majorTicks + axis.minorTicks for x in (t.label1, t.label2)} - shown
    texts = [t for t in fig.findobj(Text) if t.get_visible() and t.get_text().strip() and id(t) not in undrawn]
    for t in texts:
        if t.get_fontsize() < min_pt - 1e-6:
            issues.append(f"text {t.get_text()[:30]!r} at {t.get_fontsize():.1f} pt, below {min_pt} pt")
    boxes = [(t, t.get_window_extent(renderer)) for t in texts]
    edge = fig.bbox
    for t, b in boxes:
        if b.x0 < edge.x0 - 1 or b.y0 < edge.y0 - 1 or b.x1 > edge.x1 + 1 or b.y1 > edge.y1 + 1:
            issues.append(f"text {t.get_text()[:30]!r} is cut at the figure's edge")
    for i, (a, ba) in enumerate(boxes):
        for b, bb in boxes[i + 1:]:
            if _overlap(ba, bb):
                issues.append(f"text {a.get_text()[:25]!r} overlaps {b.get_text()[:25]!r}")
    return issues + _keys_over_data(fig, renderer)


def _keys_over_data(fig, renderer):
    """Each key (legend) of an axes that covers its data: scatter points, line markers or bars inside the axes and
    under the key's box (docs/adr/0047, A5)."""
    import numpy as np
    from matplotlib.collections import PathCollection
    from matplotlib.container import BarContainer
    out = []
    for i, ax in enumerate(fig.axes):
        key = ax.get_legend()
        if key is None or not key.get_visible():
            continue
        box, area = key.get_window_extent(renderer), ax.get_window_extent(renderer)
        points = [c.get_offset_transform().transform(np.asarray(c.get_offsets(), float)) for c in ax.collections
                  if isinstance(c, PathCollection) and c.get_visible() and len(c.get_offsets())]
        points += [ln.get_transform().transform(np.asarray(ln.get_xydata(), float)) for ln in ax.get_lines()
                   if ln.get_visible() and ln.get_marker() not in (None, "", "None", " ") and len(ln.get_xydata())]
        covered = 0
        for p in points:
            p = p[np.isfinite(p).all(axis=1)]
            shown = (p[:, 0] >= area.x0) & (p[:, 0] <= area.x1) & (p[:, 1] >= area.y0) & (p[:, 1] <= area.y1)
            under = (p[:, 0] >= box.x0) & (p[:, 0] <= box.x1) & (p[:, 1] >= box.y0) & (p[:, 1] <= box.y1)
            covered += int((shown & under).sum())
        bars = sum(1 for c in ax.containers if isinstance(c, BarContainer) for b in c.patches
                   if b.get_visible() and _overlap(b.get_window_extent(renderer), box))
        if covered or bars:
            what = ", ".join(x for x in (f"{covered} points" * bool(covered), f"{bars} bars" * bool(bars)) if x)
            out.append(f"the key of {ax.get_title() or f'axes {i + 1}'!r} covers data ({what}): put it above the "
                       "axes (loc='lower left', bbox_to_anchor=(0, 1)) or where no data are")
    return out


# ---- the charts published screens use (plugin/skills/pertura-knowledge/figure_patterns.md) -----------------------
CONTROL_GREY = "0.6"


def composition_bars(ax, shares, states, colours, control="control", spread=None, first=None):
    """Stacked horizontal bars of each group's state shares, the control row on top and set apart. shares: a table,
    groups by states (each row's shares summing to about 1); spread: the same shape (the spread over replicates),
    drawn as an error bar at the end of the first state's segment. Rows are drawn in the table's order."""
    rows = [control] + [g for g in shares.index if g != control]
    y = {g: (0 if g == control else i + 0.6) for i, g in enumerate(rows)}
    for g in rows:
        left = 0.0
        for st in states:
            v = float(shares.loc[g, st]) if st in shares.columns else 0.0
            ax.barh(y[g], v, left=left, height=0.72, color=colours[st], linewidth=0)
            left += v
        if spread is not None and states[0] in spread.columns and g in spread.index:
            v0 = float(shares.loc[g, states[0]])
            ax.errorbar(v0, y[g], xerr=float(spread.loc[g, states[0]]), color="black", lw=0.6, capsize=1.5,
                        capthick=0.6)
    ax.set_yticks([y[g] for g in rows])
    ax.set_yticklabels(rows)
    ax.set_ylim(max(y.values()) + 0.6, -0.6)
    ax.set_xlim(0, 1)
    return {"control_shown": True}


def density_maps(axes, xy, groups, labels=None, levels=6):
    """For each group (name: a mask over the cells), its cells' density on the embedding xy (cells by 2), over all
    cells in grey; one axis each, on the same map and scale. Groups with fewer than 10 cells show their cells only."""
    from scipy import ndimage, stats
    xy = np.asarray(xy, float)
    n = 120
    lo, hi = xy.min(0), xy.max(0)
    gx, gy = np.mgrid[lo[0]:hi[0]:n * 1j, lo[1]:hi[1]:n * 1j]
    grid = np.vstack([gx.ravel(), gy.ravel()])
    occupied, _, _ = np.histogram2d(xy[:, 0], xy[:, 1], bins=n, range=[[lo[0], hi[0]], [lo[1], hi[1]]])
    where_cells = ndimage.binary_dilation(occupied > 0, iterations=2)  # densities are drawn only where cells lie
    for ax, (name, mask) in zip(axes, groups.items()):
        ax.scatter(xy[:, 0], xy[:, 1], s=0.1, color="0.88", linewidths=0, rasterized=True)
        sel = xy[np.asarray(mask, bool)]
        if len(sel) >= 10:
            d = stats.gaussian_kde(sel.T, bw_method=lambda k: k.scotts_factor() * 0.5)(grid).reshape(gx.shape)
            d = np.where(where_cells, d, np.nan)
            top = np.nanmax(d)
            ax.contourf(gx, gy, d, levels=np.linspace(top * 0.2, top, levels), cmap="Blues", alpha=0.9)
        else:
            ax.scatter(sel[:, 0], sel[:, 1], s=2, color=OKABE_ITO[0], linewidths=0)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(0.03, 0.97, (labels or {}).get(name, name), transform=ax.transAxes, fontsize=6, va="top",
                fontstyle="italic" if name != "control" else "normal")
    return {"control_shown": "control" in groups}


def marker_dotplot(ax, table, genes, states, state_labels=None):
    """Marker genes (columns) by state (rows): dot size the share of cells detecting the gene, shade its mean
    expression. table: gene, state, share expressing, mean."""
    top = table["mean"].max() if len(table) else 1.0
    for i, st in enumerate(states[::-1]):
        for j, g in enumerate(genes):
            r = table[(table["gene"] == g) & (table["state"] == st)]
            if len(r):
                ax.scatter(j, i, s=2 + 40 * float(r["share expressing"].iloc[0]), c=[float(r["mean"].iloc[0])],
                           cmap="Greys", vmin=0, vmax=top, linewidths=0.3, edgecolors="0.3")
    ax.set_xticks(range(len(genes)))
    ax.set_xticklabels(genes, rotation=60, ha="right", fontstyle="italic")
    ax.set_yticks(range(len(states)))
    ax.set_yticklabels([(state_labels or {}).get(s, s) for s in states[::-1]])
    ax.set_xlim(-0.6, len(genes) - 0.4)
    ax.set_ylim(-0.6, len(states) - 0.4)
    return {}


def effects_with_replicates(ax, table, order, group="group", unit="unit", value="value", mean_colour=None):
    """Each group's value per replicate as dots (shades of grey by replicate), its mean as a short bar, rows in
    `order` (top to bottom), a dashed line at zero. table: group, unit, value."""
    units = sorted(table[unit].unique())
    shade = dict(zip(units, np.linspace(0.15, 0.75, max(len(units), 1))))
    for yi, g in enumerate(order):
        d = table[table[group] == g]
        for _, r in d.iterrows():
            off = (units.index(r[unit]) - (len(units) - 1) / 2) * 0.12
            ax.scatter(r[value], yi + off, s=7, color=str(shade[r[unit]]), linewidths=0, zorder=3)
        if len(d):
            ax.vlines(d[value].mean(), yi - 0.34, yi + 0.34, color=mean_colour or OKABE_ITO[3], lw=1.2, zorder=4)
    ax.axvline(0, color="0.6", lw=0.5, ls="--", zorder=0)
    ax.set_ylim(len(order) - 0.5, -0.5)
    ax.set_yticks(range(len(order)))
    return {}


def save_figure_files(fig, stem, checks=True):
    """The figure at its own size (no cropping, so its width stays a column width): PDF and SVG with editable text, and
    a 300 dpi PNG; with checks, the issues check_figure finds (a non-empty list means it should not enter the
    record)."""
    issues = check_figure(fig) if checks else []
    for ext in ("pdf", "svg", "png"):
        fig.savefig(f"{stem}.{ext}", dpi=300)
    return issues


# ---- a figure as an entry of the record (docs/adr/0021, section 2) -------------------------------------------------
ROLES = ("overview", "detail", "evidence", "limitation")  # a tile's role in a figure set
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
SIDECAR = ".figure.json"


def to_journal_width(fig):
    """A figure whose width is not a column width set to the nearer one (89 or 183 mm) and laid out again; returns
    what was changed, or None (docs/adr/0024, section 2: a width between columns is a mechanical fix)."""
    width = fig.get_figwidth()
    target = SINGLE if width <= (SINGLE + DOUBLE) / 2 else DOUBLE
    if abs(width - target) * 25.4 <= 1.0:
        return None
    from matplotlib.layout_engine import PlaceHolderLayoutEngine
    fig.set_figwidth(target)
    engine = fig.get_layout_engine()
    if engine is None or isinstance(engine, PlaceHolderLayoutEngine):  # after tight_layout(): a placeholder
        fig.tight_layout(pad=0.4)
    else:
        engine.execute(fig)
    return f"width set from {width / MM:.0f} mm to {target / MM:.0f} mm"


OVERLAP_HELP = ("overlapping text: shorter or fewer labels, a smaller font (5 to 7 pt), rotated tick labels, or the "
                "legend outside the axes; labels on points: adjustText's adjust_text moves them apart")


STRICT_FIX = ("save it again with save_figure(..., strict=True), which sets its width to a column and holds it to "
              "the layout checks")


def save_figure(fig, name, data=None, caption=None, role=None, folder="figures", checks=True, strict=False):
    """Write a figure the record can enter: the PNG at 300 dpi, a PDF with editable text, the plotted data as tables
    where given, and <name>.figure.json, which the tool that ran the code enters as figure_saved. data: a DataFrame
    (or a Series), or {name: DataFrame} for several tables. role: one of ROLES, or None until the figure is placed in
    a panel. Saving a name again makes a new version of that figure. Returns what the sidecar holds.

    A figure kept while exploring (docs/adr/0032, D4) is saved as it is: a missing caption or data is noted, and the
    layout checks note their issues (checks). strict=True, for a figure going into a panel or the report, is the
    publication standard: the caption and the data are required, a width that is not a column width is set to the
    nearer one first (and said so), and a figure that fails check_figure is not written; its issues are raised, with
    what fixes them."""
    import hashlib
    import json
    import os

    import matplotlib
    import pandas as pd
    if not NAME.match(str(name)):
        raise ValueError(f"name {name!r}: letters, digits, '_' and '-' only (it names the files)")
    has_caption = bool(str(caption or "").strip())
    if strict and not has_caption:
        raise ValueError("a figure for a panel or the report needs its caption: its content")
    words = None
    if role is not None and role not in ROLES:  # the agent's words for the figure, kept; it has no tile role yet
        words, role = str(role), None
    if isinstance(data, pd.Series):
        data = data.to_frame()
    tables = {"": data} if isinstance(data, pd.DataFrame) else dict(data or {})
    tables = {k: v.to_frame() if isinstance(v, pd.Series) else v for k, v in tables.items()}
    if (data is not None or strict) and (not tables or any(not isinstance(t, pd.DataFrame) or t.empty
                                                           for t in tables.values())):
        raise ValueError("the data a figure plots is a DataFrame, or {name: DataFrame}, with rows"
                         + (" (a figure for a panel or the report needs it)" if strict else ""))
    bad = [k for k in tables if k and not NAME.match(str(k))]
    if bad:
        raise ValueError(f"table names {bad}: letters, digits, '_' and '-' only")
    os.makedirs(folder, exist_ok=True)
    stem = os.path.join(folder, name)
    resized = to_journal_width(fig) if checks and strict else None
    with matplotlib.rc_context({"pdf.fonttype": 42, "svg.fonttype": "none"}):
        issues = check_figure(fig) if checks else []
        if issues and strict:
            fixes = (f" ({OVERLAP_HELP})" if any("overlaps" in i for i in issues) else "")
            raise ValueError(f"figure {name} fails its checks, so it was not saved"
                             + (f" ({resized})" if resized else "") + ": " + "; ".join(issues) + fixes)
        fig.savefig(stem + ".png", dpi=300, metadata={"Title": caption or name})
        fig.savefig(stem + ".pdf", metadata={"Title": caption or name})
    data_paths = []
    for key, t in tables.items():
        p = f"{stem}.data{'.' + key if key else ''}.tsv"
        t.to_csv(p, sep="\t", index=not isinstance(t.index, pd.RangeIndex))
        data_paths.append(p)
    with open(stem + ".png", "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    notes = ([] if has_caption else ["no caption"]) + ([] if data_paths else ["no plotted data"])
    said = [f"role {words!r} kept as a note: a tile's role is one of {', '.join(ROLES)}"] if words else []
    entry = {"name": name, "caption": caption if has_caption else None, "role": role, "png": stem + ".png",
             "pdf": stem + ".pdf", "data": data_paths, "checks": issues, "checked": bool(checks),
             "strict": bool(strict), "notes": notes + said, "png_sha256": digest}
    if resized:
        entry["resized"] = resized
    if issues or notes:
        entry["for_a_panel"] = STRICT_FIX + " (and its caption and data)" if notes else STRICT_FIX
    tmp = stem + SIDECAR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=1)
    os.replace(tmp, stem + SIDECAR)
    return entry
