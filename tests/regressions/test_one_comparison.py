"""Item 24 of docs/build_goal_session6_fixes.md, the shared fixes beside GPT's tests: one comparison per target
(Screen.comparison), a pair's reference by the user's decision (BR34), a target whose guides span arms split at the
design check (BR26), each block call's counts by target (BR37), one set of words for states (24.3) and what a result
compared (24.4)."""
import json

import numpy as np
import pytest

from regression_helpers import guide, node
from pertura import blocks as pt
from pertura import result_states
from pertura.question_modules.contract import compared


def mixed(make_screen):
    gs = [guide("a1", "A"), guide("b1", "B"), guide("c1", "C", "CRISPRa"), guide("ni", "NTi"),
          guide("na", "NTa", "CRISPRa")]
    rows = [("A", "a1", "u", [1, 2, 3], 12), ("B", "b1", "u", [1, 2, 3], 12), ("C", "c1", "u", [3, 2, 1], 12),
            ("A|B", "a1:5;b1", "u", [1, 2, 3], 12), ("A|C", "a1:5;c1", "u", [1, 2, 3], 12),
            ("control", "ni", "u", [1, 2, 3], 20), ("control", "na", "u", [3, 2, 1], 30)]
    return make_screen(rows, gs, matches=["mode"])


def test_a_pairs_reference_its_arm_or_every_control_cell(make_screen):
    s = mixed(make_screen)
    arm_i = s.reference(target="A")
    assert int(arm_i.sum()) == 20 and int(s.reference(target="C").sum()) == 30
    assert (s.own_reference("A|B") == arm_i).all()  # a pair of one mode: its mode's control cells
    assert int(s.own_reference("A|C").sum()) == 50  # a CRISPRi and a CRISPRa guide: every control cell (BR34)
    assert s.arm_or_every("A|C") == "every arm"
    c = s.comparison("A", unit="u")
    assert (c["counts"], c["arm"]) == ({"cells": 12, "reference_cells": 20}, json.dumps(["CRISPRi"]))
    assert s.comparison("A", every_arm=True)["counts"]["reference_cells"] == 50


def test_a_target_whose_guides_span_arms_is_refused_and_the_design_check_proposes_a_split(make_screen):
    from pertura.design_audit import checks
    gs = [guide("t1", "T", vector="v1"), guide("t2", "T", vector="v2"), guide("n1", "NT1", vector="v1")]
    s = make_screen([("T", "t1", "u", [1, 2, 3], 10), ("T", "t2", "u", [1, 2, 3], 10),
                     ("control", "n1", "u", [1, 2, 3], 10)], gs, matches=["vector"])
    with pytest.raises(ValueError, match="split it"):
        s.reference(target="T")
    problems = []
    checks._guides(s.design, {"guides": [g["guide"] for g in gs], "genes": list(s.genes)}, problems)
    assert any("different vector" in p for p in problems), problems


def test_a_block_calls_counts_say_each_targets_own_reference(make_screen, tmp_path):
    s = mixed(make_screen)
    n = pt._n(s, ["A", "C"], "control_guides")
    assert n["reference_cells_by_target"] == {"A": 20, "C": 30}


def test_the_words_for_runs_and_results():
    assert [result_states.run_state(x) for x in ("ok", "done", "stopped", "running")] == [
        "done", "done", "failed", "running"]
    assert result_states.result_words({"status": "not_enough_data", "summary": "too few cells"}) == {
        "result": "not testable", "why": "too few cells"}
    assert result_states.result_state("detected") == "found"


def test_a_result_says_what_it_compared(make_screen):
    import pandas as pd
    s = mixed(make_screen)
    said = compared(s, pd.DataFrame({"target": ["A", "C", "A"]}))
    assert said["A"] == {"cells": 12, "reference_cells": 20, "reference": "the control cells of its arm (CRISPRi)",
                         "units": 1}
    assert said["C"]["reference_cells"] == 30
