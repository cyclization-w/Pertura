"""A figure's key does not cover its data (docs/adr/0047, A5): in the runs of 2026-11-11 two keys sat over data
points. check_figure, which save_figure runs, names a key over scatter points, line markers or bars."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from pertura.question_modules.figures import MM, check_figure  # noqa: E402


def scatter(where):
    fig, ax = plt.subplots(figsize=(89 * MM, 60 * MM))
    x, y = np.meshgrid(np.linspace(0, 1, 20), np.linspace(0, 1, 20))
    ax.scatter(x.ravel(), y.ravel(), s=4, color="0.6", label="Targets")
    ax.scatter([0.5], [0.5], s=6, color="C3", label="Control guides")
    if where == "inside":
        ax.legend(loc="upper right", fontsize=6)
    else:
        ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, fontsize=6, frameon=False)
    return fig


def covered(fig):
    out = [i for i in check_figure(fig) if "covers data" in i]
    plt.close(fig)
    return out


def test_a_key_over_scatter_points_is_named():
    issues = covered(scatter("inside"))
    assert len(issues) == 1 and "points" in issues[0], issues


def test_a_key_above_the_axes_covers_nothing():
    assert covered(scatter("above")) == []


def test_a_key_over_bars_is_named():
    fig, ax = plt.subplots(figsize=(89 * MM, 60 * MM))
    ax.bar(range(6), [5, 6, 7, 8, 9, 10], color="C0", label="Genes changed")
    ax.legend(loc="upper right", fontsize=6)
    issues = covered(fig)
    assert len(issues) == 1 and "bars" in issues[0], issues


def test_a_key_over_a_plain_line_is_not_named():
    fig, ax = plt.subplots(figsize=(89 * MM, 60 * MM))
    ax.plot(np.linspace(0, 1, 50), np.linspace(0, 1, 50), color="C0", label="Trend")
    ax.legend(loc="upper left", fontsize=6)
    assert covered(fig) == []
