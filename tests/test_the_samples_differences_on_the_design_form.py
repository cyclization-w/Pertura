"""The design form names the characteristics the deposit's samples differ in (docs/adr/0045, D7): GSE335887's two
microglia models were recorded as neither a condition nor a unit, so the survey could not set them side by side."""
from pertura.design_audit import inventory as inv
from pertura.design_service import measured_flags

GEO = {"samples": {"GSM1": {"characteristics": {"cell type": ["iMG"], "time": ["0h"], "donor": ["d1"]}},
                   "GSM2": {"characteristics": {"cell type": ["iTF-MG"], "time": ["0h"], "donor": ["d1"]}},
                   "GSM3": {"characteristics": {"cell type": ["iTF-MG"], "time": ["6h"], "donor": ["d1"]}}}}


def test_the_characteristics_with_two_levels_or_more_are_measured():
    facts, questions = inv.samples_differ(GEO)
    assert facts[0]["key"] == "experiment.samples_differ" and questions == []
    assert facts[0]["value"] == {"cell type": ["iMG", "iTF-MG"], "time": ["0h", "6h"]}  # donor d1 throughout
    assert inv.samples_differ(GEO, {"GSM1", "GSM2"})[0][0]["value"] == {"cell type": ["iMG", "iTF-MG"]}
    assert inv.samples_differ(None) == ([], [])


def test_the_conditions_item_on_the_form_names_them():
    facts = {f["key"]: f for f in inv.samples_differ(GEO)[0]}
    words = measured_flags(facts, ["experiment.conditions"])["experiment.conditions"][0]
    assert words.startswith("the deposit's samples differ in cell type (iMG, iTF-MG); time (0h, 6h)")
    assert "a condition, a unit, a batch, or not compared" in words
    assert measured_flags(facts, ["units.batches"]) == {}  # only where the conditions are asked
