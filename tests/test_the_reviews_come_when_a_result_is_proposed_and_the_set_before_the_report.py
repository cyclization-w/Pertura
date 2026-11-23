"""The skeptic reads each result once it is proposed; the figures are reviewed once, as a set, before the report
(on GSE267740, d3a7dcc, a review per result's figure changed layouts and captions, no reading). Both were once due only at the end (docs/adr/0047, A6: in the runs of 2026-11-11 each result drew
its own reviews as it came, and each review sent its result to be reported again). On the three runs of 2b6a17a and
99a1f49 the reviews then came 10-30 minutes before the end, and 43 of 47 doubts were stated as limits with no run (the
fresh review of 2026-11-13, #2). A result read stays read."""
from pertura import record as rc


def state(reviews=(), enough=None):
    return {"dossiers": {}, "figures": {"fig1": {"version": 1, "reviews": []}}, "set_reviews": [],
            "findings": {"f1": {}, "f2": {}},
            "results": {"r1": {"result_id": "r1", "finding_ids": ["f1", "f2"], "figure": "fig1", "seq": 9,
                               "decision": None}},
            "reviews": list(reviews), "enough": enough}


PLANNED = {"verdict": "not yet", "story": "s", "figures": [{"claim": "c", "panels": ["a"]}], "seq": 3}
COMPLETE = {"verdict": "complete", "story": "s", "figures": [{"claim": "c", "panels": ["a"]}], "seq": 12}


def test_a_result_read_by_the_skeptic_is_not_due_again():
    st = state([PLANNED, COMPLETE])
    st["results"]["r1"]["skeptic"] = True
    st["findings"]["f1"]["skeptic"] = st["findings"]["f2"]["skeptic"] = True
    assert rc.skeptic_due(st) == []
