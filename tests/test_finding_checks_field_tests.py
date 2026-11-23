"""A finding's checks after the open points' F5: guides agree only when enough are significant its way, their line
in numbers; the field's other tests as a table, each beside the same test on control-guide groups."""
import numpy as np
import pytest

from pertura import finding_checks as fc
from pertura import screen_object as so
from pertura.question_modules.data import Screen

from test_cells_overview import DESIGN, _deposit


def r(effect, q):
    return {"effect": effect, "p": q, "q": q}


def test_guides_agree_only_when_two_are_significant_its_way():
    level = 0.05
    assert fc.parts_outcome({"g1": r(-1, 0.01), "g2": r(-0.8, 0.02)}, -1, level) == "agree"
    # two of three guides show nothing significant: partly, never agree (session 7's Hdac1)
    assert fc.parts_outcome({"g1": r(-1, 0.01), "g2": r(-0.1, 0.6), "g3": r(-0.05, 0.9)}, -1, level) == "partly"
    assert fc.parts_outcome({"g1": r(-1, 0.01), "g2": r(0.9, 0.01)}, -1, level) == "disagree"
    assert fc.parts_outcome({"g1": r(-1, 0.01), "g2": r(0.1, 0.7), "g3": r(0.2, 0.6)}, -1, level) == "disagree"
    words = fc.parts_counts({"g1": r(-1, 0.01), "g2": r(-0.1, 0.6), "g3": r(0.05, 0.9)}, -1, level)
    assert words == "1 of 3 significant its way, 1 its way, not significant, 1 the other way, not significant"


@pytest.fixture
def screen(tmp_path):
    out = tmp_path / "screen"
    so.build(str(_deposit(tmp_path)), DESIGN, str(out))
    return Screen(str(out), DESIGN)


def test_the_other_tests_each_with_its_outcome(screen):
    seen = []

    def measure(s, targets, method, alone=False):
        seen.append((method, tuple(targets), alone))
        return {t: {"wilcoxon": r(-1.0, 0.001), "mast": r(-0.4, 0.3), "pydeseq2": r(0.7, 0.01)}[method]
                for t in targets}
    spec = {"target": "A", "direction": -1, "kind": "gene", "feature": "G1"}
    plan = {"check": "tests", "methods": [{"method": "wilcoxon", "label": "Wilcoxon on cells"},
                                          {"method": "mast", "label": "MAST on cells"}]}
    out = fc._tests(plan, screen, spec, measure, 0.05, 0)
    assert [x["outcome"] for x in out["rows"]] == ["agrees", "its way, not significant"]
    assert out["outcome"] == "partly"
    assert all(a for _, _, a in seen)  # the finding's gene alone
    plan["methods"].append({"method": "pydeseq2", "label": "DESeq2 pseudobulk by sample"})
    assert fc._tests(plan, screen, spec, measure, 0.05, 0)["outcome"] == "other direction"
    words = fc.sentence(spec, {"outcome": "agrees", "effect": -1.0, "q": 0.001, "test": "wilcoxon", "cells": 40},
                        [fc._tests(plan, screen, spec, measure, 0.05, 0)])
    assert "the field's other tests: other direction (Wilcoxon on cells: effect -1, p 0.001, agrees" in words


def test_a_test_that_fails_says_so_and_the_others_still_run(screen):
    def measure(s, targets, method, alone=False):
        if method == "mast":
            raise RuntimeError("R is not here")
        return {t: r(-1.0, 0.001) for t in targets}
    spec = {"target": "A", "direction": -1, "kind": "gene", "feature": "G1"}
    plan = {"check": "tests", "methods": [{"method": "mast", "label": "MAST on cells"},
                                          {"method": "wilcoxon", "label": "Wilcoxon on cells"}]}
    out = fc._tests(plan, screen, spec, measure, 0.05, 0)
    assert out["rows"][0]["outcome"] == "not run" and "R is not here" in out["rows"][0]["why"]
    assert out["outcome"] == "agree"
    assert np.isnan(out["rows"][0]["effect"])
