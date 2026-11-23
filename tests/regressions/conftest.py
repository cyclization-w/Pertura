"""Fixtures of the regression tests: synthetic screens built through the real AnnData and Screen, figure rendering
left out (these tests assert what is compared, not how it is drawn), and the TF-activity backend replaced by a
recorder of its input (decoupler is not installed on every machine; the Fisher test stays real)."""
import json
import sys
import types

import anndata as ad
import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from regression_helpers import design_for, guide
from pertura.question_modules.data import Screen


@pytest.fixture
def make_screen(tmp_path):
    counter = 0

    def make(rows, guides, matches=(), design=None, genes=None):
        """rows: (label, guide ID or serialized guide text, unit, counts, n)."""
        nonlocal counter
        counter += 1
        folder = tmp_path / f"screen{counter}"
        folder.mkdir()
        expanded = [(lab, g, u, x) for lab, g, u, x, n in rows for _ in range(n)]
        names = genes or [f"g{i}" for i in range(len(expanded[0][3]))]
        ids = {g["guide"] for g in guides}
        obs = pd.DataFrame({
            "label": [r[0] for r in expanded],
            "guides": [r[1] + ":10" if r[1] in ids else r[1] for r in expanded],
            "unit": [r[2] for r in expanded], "sample": [r[2] for r in expanded],
            "control_group": ["NT" if r[0] == "control" else "" for r in expanded]},
            index=[f"c{i}" for i in range(len(expanded))])
        a = ad.AnnData(sp.csr_matrix(np.asarray([r[3] for r in expanded], float)),
                       obs=obs, var=pd.DataFrame(index=names))
        pd.DataFrame(guides).to_csv(folder / "guides.tsv", sep="\t", index=False)
        (folder / "screen.json").write_text(json.dumps({"unit_source": "column", "flags": []}), encoding="utf-8")
        return Screen(str(folder), design if design is not None else design_for(guides, matches), adata=a)
    return make


@pytest.fixture
def observe(request):
    """Keeps a test's measured values with the test (shown by -rA when it fails); writes no file."""
    seen = {}

    def save(**facts):
        seen.update(facts)
        request.node.user_properties.append(("observed", dict(facts)))
    return save


@pytest.fixture(autouse=True)
def rendering_only(monkeypatch):
    from pertura.question_modules import charts, figures
    for module, names in [(figures, ["bars", "dots", "condition_map"]),
                          (charts, ["checks", "two_scores", "ranked_targets"])]:
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda *a, **kw: {})


@pytest.fixture
def matched_screen(make_screen):
    guides = [guide("t", "T", gene="g0"), guide("ni", "NTi"), guide("na", "NTa", "CRISPRa")]
    return make_screen([("T", "t", "u1", [0, 30, 20, 10], 40),
                        ("control", "ni", "u1", [0, 30, 20, 10], 40),
                        ("control", "na", "u1", [30, 10, 20, 10], 40)], guides, matches=["mode"])


@pytest.fixture
def direct_call(monkeypatch, tmp_path):
    """Records what reaches the ULM backend; keeps the real Fisher test."""
    from pertura.question_modules import g
    captured = []

    def ulm(*, data, net, tmin):
        captured.append(data.copy())
        es = pd.DataFrame(-1., index=data.index, columns=["g0"])
        return es, es * 0 + .5
    fake = types.ModuleType("decoupler")
    fake.mt = types.SimpleNamespace(ulm=ulm)
    monkeypatch.setitem(sys.modules, "decoupler", fake)

    def call(screen, de, net=None, targets=None):
        """The module's result and everything its calls of the backend received (one frame, however many
        calls)."""
        net = net if net is not None else pd.DataFrame({"source": ["g0", "g0"], "target": ["a", "b"],
                                                        "weight": [1., 1.]})
        start = len(captured)
        result = g.direct_targets(screen, screen.design, str(tmp_path), de=de, net=net, targets=targets, tmin=1)
        return result, pd.concat(captured[start:]) if len(captured) > start else pd.DataFrame()
    return call
