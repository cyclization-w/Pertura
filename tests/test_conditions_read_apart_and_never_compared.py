"""Conditions read apart and never compared (GSE267740 on 2b6a17a: two disease models run as separate experiments; the
user refused "two conditions", "no conditions" and "two experiments" on the form, because none could say "each read
apart, never compared", and the research lead then opened a thread comparing them). A factor's "compared": false keeps
each condition's own reading and drops what sets them against each other: the survey's comparisons across them, the
kind of conclusion "conditions apart", and the form says which it is."""
from test_the_survey_of_the_designs_comparisons import cells_of, node, screen

from pertura import design_gate, survey
from pertura.analysis_service import AnalysisService
from pertura.design_service import form_value

APART = {"column": "experiment", "names": ["NTN", "colitis"], "compared": False}


def design(conditions):
    return {"experiment": {"conditions": node(conditions)}, "units": {"independent_unit": node("mouse")}}


def test_a_factor_is_compared_unless_the_design_says_not():
    assert design_gate.condition_factors({"column": "experiment", "names": ["NTN", "colitis"]})[0]["compared"]
    assert not design_gate.condition_factors(APART)[0]["compared"]
    crossed = [{"name": "tissue", "levels": ["kidney", "gut"], "column": "tissue", "compared": False},
               {"name": "time", "levels": ["d7", "d14"], "column": "time"}]
    assert [f["compared"] for f in design_gate.condition_factors(crossed)] == [False, True]


def test_the_survey_reads_each_condition_and_sets_none_against_another(tmp_path):
    sd = screen(tmp_path, cells_of(["NTN", "colitis"]))
    rows = {c["row"] for c in survey.comparisons(design(APART), sd)}
    assert "shifts_per_condition" in rows  # each condition's own shifts, against its own controls
    assert not rows & {"controls_across", "effect_changing"}
    compared = {c["row"] for c in survey.comparisons(design({**APART, "compared": True}), sd)}
    assert {"controls_across", "effect_changing"} <= compared


def test_the_form_says_whether_the_conditions_are_compared():
    assert form_value(design(APART), "experiment.conditions").startswith("NTN, colitis; each read apart and never "
                                                                         "compared")
    assert form_value(design({"column": "experiment", "names": ["NTN", "colitis"]}),
                      "experiment.conditions").startswith("NTN, colitis, from")  # compared, as before: said by its line


def test_the_kind_conditions_apart_applies_only_to_compared_conditions(tmp_path):
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    svc._screen_dir, svc._module_runs = (lambda: str(tmp_path)), (lambda st: {})
    for given, applies in ((APART, False), ({**APART, "compared": True}, True)):
        svc._confirmed = lambda: design(given)
        assert svc._reach_of_the_design({})["conditions_apart"][0] is applies
