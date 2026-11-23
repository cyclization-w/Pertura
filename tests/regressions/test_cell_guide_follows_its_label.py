"""M7 of the bug register (BR27): a cell's guide, read by the analyses of guides, is the guide its label rests on."""
from regression_helpers import guide


def test_a_targets_cell_with_a_stronger_control_guide_counts_under_the_targets_guide(make_screen, observe):
    gs = [guide("t1", "T"), guide("t2", "T"), guide("n1", "NT1")]
    s = make_screen([("T", "t1:3;n1:5", "u", [1, 2, 3], 4), ("T", "t2:5;t1:5", "u", [1, 2, 3], 2),
                     ("T", "t1:5;t2:5", "u", [1, 2, 3], 2), ("control", "n1:7", "u", [1, 2, 3], 4),
                     ("none", "t1:2;n1:2", "u", [1, 2, 3], 1)], gs)
    top = list(s.top_guide)
    observe(top_guides=top)
    assert top[:4] == ["t1"] * 4  # its target's guide, not the control guide with more UMIs
    assert top[4:8] == ["t1"] * 4  # a tie goes to the guides' order, whatever the order they are listed in
    assert top[8:12] == ["n1"] * 4
    assert top[12] == "n1"  # a cell of no single label: its strongest guide, a tie by the guides' order
