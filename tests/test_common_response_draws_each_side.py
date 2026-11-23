"""D.common_response's test draws each side to at most steps.any_effect.CAP cells, as the overview's and the block's
test do (2026-11-05: with every cell, 9 targets of GSE203592 took 2 h 11 min, a 17,523-cell target against three times
its cells with 1,000 permutations; 6 of the 10 recorded runs were stopped or failed)."""
from unittest import mock

import pandas as pd

from pertura.question_modules import c
from pertura.steps import any_effect as step


def test_the_plain_test_is_drawn_as_the_overviews(tmp_path):
    with mock.patch.object(step, "test", return_value=(pd.DataFrame(), [], {})) as called:
        c.any_effect(mock.Mock(), {}, str(tmp_path), reference="other_targets", max_reference=3)
    assert called.call_args.kwargs["draw"] == step.CAP == 1000
