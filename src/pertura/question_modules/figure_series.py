"""A finding's figure as published screens build it (docs/build/adr0022/07_figure_series_from_papers.md): several
panels, each a chart of charts.py or one's own drawing, in its part of one figure 183 mm wide, lettered a, b, c in
reading order, laid out together and checked as one figure (figures.check_figure).

A figure is given as rows: [(height in mm, [(width share, draw), ...]), ...], where draw(panel) draws into the panel
(a matplotlib subfigure): a chart, for example lambda p: charts.volcano(table, None, "", fig=p), or one's own code,
for example lambda p: p.add_subplot().plot(x, y). A panel may carry its title, what it shows in a short sentence:
(width share, draw, title).
"""
import os
import string
import textwrap

from . import figures

LETTER_PT = 8
TITLE_PT = 6


def _letter_and_title(sf, letter, title, width):
    """A panel's letter, bold at its top left, and its title beside it, wrapped to the panel's width (inches). The
    layout keeps room above the panel for a subfigure's title only when the title's height is left to it (no y), so
    the letter, or the title with the letter beside it, is that title."""
    if not title:
        sf.suptitle(letter, x=0.0, ha="left", fontsize=LETTER_PT, fontweight="bold")
        return
    from matplotlib.transforms import ScaledTranslation
    indent = 1.6 * LETTER_PT / 72  # inches: the letter and a space
    chars = max(12, int((width - indent) * 72 / (0.5 * TITLE_PT)))  # about half the font size per character
    t = sf.suptitle(textwrap.fill(str(title), chars), x=0.0, ha="left", fontsize=TITLE_PT)
    t.set_transform(sf.transSubfigure + ScaledTranslation(indent, 0, sf.figure.dpi_scale_trans))
    sf.text(0.0, 1.0, letter, transform=sf.transSubfigure, ha="left", va="top", fontsize=LETTER_PT,
            fontweight="bold")


def figure(rows, width=figures.DOUBLE):
    """The rows of panels drawn into one figure, lettered and laid out; returns the figure and the panels' letters in
    order. Nothing is saved: in a notebook, save_figure(fig, name, data, caption, role, strict=True) enters it in the
    record; a module's figure is saved by compose."""
    plt = figures._plt()
    height = sum(h for h, _ in rows) * figures.MM
    fig = plt.figure(figsize=(width, height), layout="constrained")
    fig.get_layout_engine().set(w_pad=1.5 / 72, h_pad=1.5 / 72, wspace=0.02, hspace=0.02)
    row_figs = fig.subfigures(len(rows), 1, height_ratios=[h for h, _ in rows], squeeze=False)[:, 0]
    letters, k = [], 0
    for rf, (_, panels) in zip(row_figs, rows):
        shares = [p[0] for p in panels]
        subs = rf.subfigures(1, len(panels), width_ratios=shares, squeeze=False)[0]
        for sf, (share, draw, *title) in zip(subs, panels):
            letter = string.ascii_lowercase[k]
            k += 1
            draw(sf)
            _letter_and_title(sf, letter, title[0] if title else None, width * share / sum(shares))
            letters.append(letter)
    fig.canvas.draw()  # the layout is final: the panels' labels are placed now (charts._after_layout)
    for place in fig.__dict__.pop("_after_layout", []):
        place()
    return fig, letters


def compose(rows, path, title, width=figures.DOUBLE):
    """Draws the rows of panels into one figure, letters them, and saves it; returns the layout checks' issues (empty
    when none) and the panels' letters in order."""
    plt = figures._plt()
    fig, letters = figure(rows, width)
    issues = figures.check_figure(fig)
    fig.savefig(path, metadata={"Title": title}, dpi=300)
    fig.savefig(os.path.splitext(path)[0] + ".pdf", metadata={"Title": title})
    plt.close(fig)
    return issues, letters
