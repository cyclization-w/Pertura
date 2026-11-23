"""M2 of the bug register: an unmeasured effect or correlation is not an observed zero; refusing is allowed."""
import numpy as np
import pandas as pd
import pytest

from pertura import blocks as pt
from pertura.steps import effects_across_targets as eat
from regression_helpers import guide


# BR08: a gene not tested for a target is not sent to the backend as 0
def test_missing_de_effect_is_not_fabricated_zero(make_screen, direct_call, observe):
    s = make_screen([("T", "t", "u", [1, 2, 3], 10)], [guide("t", "T", gene="g0")])
    de = pd.DataFrame({"target": ["T", "OTHER"], "gene": ["a", "b"], "effect": [-1., 1.], "q": [.01, .01]})
    try:
        _, matrix = direct_call(s, de)
    except ValueError:
        return
    value = float(matrix.loc["T", "b"]) if "T" in matrix.index and "b" in matrix.columns else None
    observe(unmeasured_T_b_sent_to_ulm=value)
    assert value is None or not np.isfinite(value)


def capture_cluster(monkeypatch):
    seen = {}

    def clusters(X, names, *a, **kw):
        seen.update(X=np.asarray(X), names=list(names), distance=kw.get("distance"))
        return {t: i % 2 for i, t in enumerate(names)}, .5
    monkeypatch.setattr(eat, "clusters", clusters)
    monkeypatch.setattr(eat, "stability", lambda *a, **kw: {0: 1., 1: 1.})
    return seen


# BR30: a target missing an effect is left out of the clustering, not given 0
def test_missing_program_effect_is_not_zero_in_clustering(matched_screen, monkeypatch, observe):
    rows = [{"target": t, "score": p, "effect": float(i + 1)} for i, t in enumerate(["T", "U", "V", "W"])
            for p in ["A", "B"] if (t, p) != ("T", "B")]
    table = pd.DataFrame(rows)
    seen = capture_cluster(monkeypatch)
    result = pt.Result("scores", {}, table, {}, [], "source")
    try:
        pt._cluster_targets(matched_screen, ["T", "U", "V", "W"], "control_guides", of=result)
    except ValueError:
        return
    if "T" not in seen["names"] or seen["X"].shape[1] < 2:
        return
    value = float(seen["X"][seen["names"].index("T"), 1])
    observe(unmeasured_T_B_sent_to_clustering=value)
    assert not np.isfinite(value)


# BR30: a target of an unobserved pair is left out, the pair not given r = 0
def test_missing_pair_correlation_is_not_zero_in_clustering(matched_screen, monkeypatch, observe):
    table = pd.DataFrame({"group": ["T", "U", "V"], "other": ["U", "V", "W"], "correlation": [.8, .7, .6],
                          "kind": "different targets"})
    seen = capture_cluster(monkeypatch)
    result = pt.Result("similarity", {}, table, {}, [], "source")
    try:
        pt._cluster_targets(matched_screen, ["T", "U", "V", "W"], "control_guides", of=result)
    except ValueError:
        return
    if not {"T", "W"} <= set(seen["names"]):
        return
    i, j = seen["names"].index("T"), seen["names"].index("W")
    value = float(seen["X"][i, j])
    distance = float(seen["distance"][i, j])
    observe(unobserved_T_W_correlation=value, supplied_T_W_distance=distance)
    assert not np.isfinite(value)
