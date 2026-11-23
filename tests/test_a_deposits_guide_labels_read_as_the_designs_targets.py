"""GSE203592 on 99eab77: the owner confirmed the authors' guide column as the labels. The build kept its 36 guide
names (and zMulti-1, zNone-1) as labels and no target of the design, every module ran per guide, and none of the
run's 213 claims could be checked; nothing blocked. The notebook's cells also held the 8 captures where the design
merged two into one unit, and the agent merged them by hand in 7 runs."""
import tempfile

import numpy as np
import pandas as pd

from pertura import design_check
from pertura import screen_object as so
from test_common_response_and_field_measures import build_screen
from test_screen_object import _deposit


def node(v):
    return {"value": v, "status": "confirmed", "source": "t"}


TARGET_MAP = {"A": ["A_1", "A_2"], "B": ["B_1"]}


def design(column="guide"):
    return {"perturbation": {"target_map": node(TARGET_MAP),
                             "controls": node([{"name": "NT", "kind": "non_targeting", "guides": ["NT_1"]}]),
                             "assignment": node({"rule": "labels", "column": column})},
            "units": {"independent_unit": node("mouse")}}


def test_a_guides_label_is_its_target_and_the_deposits_words_for_no_target_hold_none(tmp_path):
    d = _deposit(tmp_path)
    guide = ["A_1"] * 12 + ["A_2"] * 8 + ["B_1"] * 20 + ["NT_1"] * 10 + ["zMulti-1"] * 6 + ["zNone-1"] * 4
    pd.DataFrame({"cell_barcode": [f"C{i:03d}-1" for i in range(60)], "guide": guide}).to_csv(
        d / "cell_metadata.csv", index=False)
    s = so.build(str(d), design(), str(tmp_path / "screen"))
    assert {k: s["labels"][k] for k in ("A", "B", "control", "ambiguous")} == {"A": 20, "B": 20, "control": 10,
                                                                               "ambiguous": 10}
    assert not {"A_1", "B_1", "zMulti-1", "zNone-1"} & set(s["labels"])
    checks = {x["check"]: x for x in s["reading_checks"]}
    assert "library targets without cells" not in checks
    assert checks["labels outside the design"]["labels"] == ["zMulti-1", "zNone-1"]


def tables(values):
    return {"samples": ["S1"], "columns": {"guide": {"S1": values}}, "guides": set()}


def test_the_check_refuses_labels_that_give_no_target_cells_and_takes_a_guides_label_as_its_target():
    assert design_check.assignment_holds(design(), tables({"A_1": 12, "B_1": 20, "NT_1": 10, "zMulti-1": 6})) == []
    assert design_check.assignment_holds(design(), tables({"A": 20, "B": 20, "NT": 10})) == []
    out = design_check.assignment_holds(design(), tables({"C_1": 30, "D_2": 20, "NT_1": 10}))
    assert len(out) == 1 and out[0].startswith("perturbation.assignment: the labels in column 'guide' give no cell "
                                               "to any of the design's 2 targets (A, B)")
    assert "C_1 (30), D_2 (20), NT_1 (10)" in out[0]


def test_the_cells_unit_is_the_merged_unit_and_a_screen_made_again_keeps_the_captures():
    from pertura.question_modules.data import Screen
    merged = {"perturbation": {"modality": node("CRISPRi")},
              "units": {"independent_unit": node("mouse"), "merge": node({"u3": "u2"})}}
    with tempfile.TemporaryDirectory() as d:
        s, _ = build_screen(d, {"T1": (20, {}), "T2": (20, {})}, units=("u1", "u2", "u3"), design=merged)
        again = Screen(s.dir, s.design, adata=s.adata)
    obs = s.adata.obs
    assert sorted(set(obs["unit"])) == ["u1", "u2"] and list(obs["unit"]) == list(s.unit)
    assert sorted(set(obs["capture"])) == ["u1", "u2", "u3"]
    assert list(again.capture) == list(s.capture) and list(again.unit) == list(s.unit)
    assert np.array_equal(s.capture == "u3", (obs["capture"] == "u3").to_numpy())
