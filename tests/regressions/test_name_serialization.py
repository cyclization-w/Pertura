"""M3 of the bug register: identities survive names that hold separators, or are refused explicitly."""
import pandas as pd
import pytest

from regression_helpers import guide
from pertura import cell_sets, design_gate, screen_object


# BR11: an arm's key keeps its attributes apart (JSON of the list)
def test_attribute_tuples_do_not_collapse_under_pipe_join(make_screen, observe):
    gs = [guide("t", "T", element="p|q", vector="r"), guide("n1", "NT1", element="p|q", vector="r"),
          guide("n2", "NT2", element="p", vector="q|r")]
    s = make_screen([("T", "t", "u", [1, 2, 3], 10), ("control", "n1", "u", [1, 2, 3], 10),
                     ("control", "n2", "u", [3, 2, 1], 10)], gs, matches=["element", "vector"])
    try:
        n = int(s.reference(target="T").sum())
    except ValueError:
        return
    observe(actual_reference_cells=n, expected_tuple_matching_cells=10)
    assert n == 10


# BR12: a condition's levels joined with the separator escaped
def test_crossed_condition_tuples_remain_distinct(observe):
    conditions = [{"name": "f1", "by_sample": {"s1": "a|b", "s2": "a"}},
                  {"name": "f2", "by_sample": {"s1": "c", "s2": "b|c"}}]
    try:
        names = design_gate.condition_names(conditions)
    except ValueError:
        return
    observe(condition_names=names, distinct_observed_tuples=2)
    assert len(names) == 2


# BR13: a guide's ID is read up to its last ':'
def test_guide_colon_is_read_consistently(make_screen, observe):
    s = make_screen([("T", "guide:1", "u", [1, 2, 3], 3)], [guide("guide:1", "T")])
    cells = s.adata.obs.assign(barcode=s.adata.obs_names)
    try:
        bad = cell_sets.unsupported_labels(s.dir, cells.barcode, s.label, cells)
    except ValueError:
        return
    observe(top_guides=list(s.top_guide), carrier_cells=int(s.carriers("T").sum()), unsupported_cells=len(bad))
    assert len(bad) == 0


# BR14: a single target named with '+' or '|' is refused at the design check
@pytest.mark.parametrize("literal", ["A+B", "A|B"])
def test_accepted_single_target_name_preserves_guide_support(make_screen, observe, literal):
    from pertura.design_audit import checks
    s = make_screen([(literal, "g", "u", [1, 2, 3], 3)], [guide("g", literal)])
    problems = []
    checks._target_map(s.design, {"guides": ["g"], "genes": list(s.genes)}, problems, [])
    if problems:  # an explicit refusal of the name is a valid resolution
        return
    cells = s.adata.obs.assign(barcode=s.adata.obs_names)
    bad = cell_sets.unsupported_labels(s.dir, cells.barcode, s.label, cells)
    observe(validation_problems=problems, targets=s.targets(), single_cells=int(s.single().sum()),
            unsupported=len(bad))
    assert int(s.single().sum()) == 3 and len(bad) == 0


# BR15: the sample is taken off a barcode by its name, or at the last ':'
def test_sample_colon_preserves_metadata_barcode_matching(tmp_path, observe):
    metadata = tmp_path / "meta.tsv"
    pd.DataFrame({"barcode": ["c0", "c1", "c2"], "deposit_label": ["T"] * 3}).to_csv(metadata, sep="\t", index=False)
    cells = pd.DataFrame({"barcode": ["S:one:c0", "S:one:c1", "S:one:c2"]})
    try:
        result = screen_object._labels_from_metadata({"tables": [str(metadata)]}, cells, "deposit_label")
    except ValueError as exc:
        observe(error=str(exc), expected_mapped_cells=3)
        pytest.fail(f"sample-qualified barcode lost its raw identity: {exc}")
    observe(mapped_cells=int((result.label == "T").sum()))
    assert list(result.label) == ["T"] * 3
