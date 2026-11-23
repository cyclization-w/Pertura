"""M2 and M5 of the bug register: a results table's scope and a confirmed design's units are not replaced."""
import pandas as pd
import pytest

from regression_helpers import guide, node
from pertura import blocks as pt


# BR09: a table-driven block keeps the table's targets, cut to those asked
@pytest.mark.parametrize("block", ["rank_on", "correlate_effects", "cluster_targets"])
def test_table_targets_survive_current_screen_cell_threshold(make_screen, tmp_path, monkeypatch, observe, block):
    gs = [guide(f"t{i}", f"T{i}") for i in range(6)] + [guide("n", "NT")]
    s = make_screen([(f"T{i}", f"t{i}", "u", [1, 2, 3], 10 if i < 5 else 1) for i in range(6)]
                    + [("control", "n", "u", [1, 2, 3], 10)], gs)
    table = pd.DataFrame([{"target": f"T{i}", "score": p, "effect": x}
                          for i in range(6) for p, x in [("A", float(i)), ("B", float(-i) + .01 * (i % 2))]])
    of = pt.Result("scores", {}, table, {}, [], "stored comparison", call_id="scope_fixture")
    if block == "cluster_targets":
        from pertura.steps import effects_across_targets as eat
        monkeypatch.setattr(eat, "clusters", lambda X, names, *a, **kw: ({t: i % 2 for i, t in enumerate(names)}, .5))
        monkeypatch.setattr(eat, "stability", lambda *a, **kw: {0: 1., 1: 1.})
    with pt.inside(s, str(tmp_path)):
        result = getattr(pt, block)(of, **({"up": "A", "down": "B"} if block == "rank_on" else {}))
    actual = int(result.table.targets.iloc[0]) if block == "correlate_effects" else len(result.table)
    observe(input_targets=6, output_targets=actual, screen_targets_over_threshold=len(s.targets(10)))
    assert actual == 6


# BR07: the Fisher background is the target's own tested genes
def test_fisher_background_does_not_borrow_other_targets_genes(make_screen, direct_call, observe):
    s = make_screen([("T", "t", "u", [1, 2, 3], 10)], [guide("t", "T", gene="g0")])
    own = pd.DataFrame({"target": ["T"] * 3, "gene": ["a", "b", "c"], "effect": [-1., -1., 0.], "q": [.001, .001, 1.]})
    unrelated = pd.DataFrame({"target": ["OTHER"] * 100, "gene": [f"x{i}" for i in range(100)], "effect": 0., "q": 1.})
    base, _ = direct_call(s, own)
    extended, _ = direct_call(s, pd.concat([own, unrelated], ignore_index=True))
    p1 = float(base.table.p_fisher.iloc[0])
    p2 = float(extended.table.p_fisher.iloc[0])
    observe(own_table_fisher_p=p1, with_unrelated_target_fisher_p=p2)
    assert p2 == pytest.approx(p1)


# BR23: a confirmed donor stays independent however the donors were told apart
def test_confirmed_donor_independence_survives_identification_method(matched_screen, observe):
    import copy
    s = matched_screen
    s.design = copy.deepcopy(s.design)
    s.design["units"].update(independent_unit=node("donor"), unit_column=node("donor"),
                             unit_identification=node("object_column"))
    s.meta["unit_source"] = "column"
    before = s.independent_units()
    s.design["units"]["unit_identification"] = node("genotype_demultiplexing")
    after = s.independent_units()
    observe(object_column_independent=before, genotype_demultiplexing_independent=after)
    assert before and after
