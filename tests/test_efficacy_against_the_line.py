"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 14), fixed
for any screen (docs/build_goal_session6_fixes.md, item 18.5): B.efficacy called GATA1-i1, 29% down and raising
TYROBP as its sibling guide does, a failed guide. Each guide is now described by its change in per cent and where it
falls against the 30% line, with no verdict word."""
import json
import os
import tempfile
import unittest
import warnings

import numpy as np
import pandas as pd

from pertura import question_modules as qm
from pertura.question_modules import b

CRISPRI = {"perturbation": {"modality": {"value": "CRISPRi", "status": "confirmed"}}}
CRISPRA = {"perturbation": {"modality": {"value": "CRISPRa", "status": "confirmed"}}}


def effects(change_by_guide):
    rows = []
    for g, fold in change_by_guide.items():
        for unit in ("u1", "u2"):
            rows.append({"guide": g, "target": g.split("_")[0], "unit": unit, "measurable": True,
                         "log2fc": float(np.log2(fold))})
    return pd.DataFrame(rows)


class TestAGuideAgainstTheLine(unittest.TestCase):
    def test_a_guide_29_percent_down_is_described_not_judged(self):
        v = b.judge_guides(effects({"G1_1": 0.71, "G1_2": 0.55}), CRISPRI, b.EFFICACY_LOG2FC).set_index("guide")
        self.assertEqual(v.loc["G1_1", "verdict"], "less than 30% down")
        self.assertAlmostEqual(v.loc["G1_1", "change_percent"], -29.0, places=1)
        self.assertFalse(v.loc["G1_1", "past_the_line"])
        self.assertEqual(v.loc["G1_2", "verdict"], "at least 30% down")
        self.assertTrue(v.loc["G1_2", "past_the_line"])

    def test_crispra_has_its_own_line(self):
        v = b.judge_guides(effects({"G1_1": 1.6, "G1_2": 1.2}), CRISPRA, b.EFFICACY_LOG2FC).set_index("guide")
        self.assertEqual(v.loc["G1_1", "verdict"], "at least 1.43 times up")
        self.assertEqual(v.loc["G1_2", "verdict"], "less than 1.43 times up")

    def test_the_module_says_no_fail_anywhere(self):
        from test_question_modules import synthetic_screen
        with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s, design = synthetic_screen(d)
            r = qm.REGISTRY["B.efficacy"].run(s, design, os.path.join(d, "eff"))
        text = " ".join([r.summary, json.dumps(r.diagnostics, default=str)]
                        + [" ".join(map(str, t["verdict"])) for t in r.tables.values() if "verdict" in t]).lower()
        self.assertNotIn("fail", text)
        self.assertNotIn("works", text)

    def test_the_line_describes_and_never_decides(self):
        """docs/adr/0045, amendment 1 (C): the status is a measurement, and the summary says what the line can show
        under each modality; with no guide past the line the status was "not detected", which the survey showed as
        nothing detected for a knockout screen whose transcripts stay."""
        from test_question_modules import synthetic_screen
        with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s, design = synthetic_screen(d)
            r = qm.REGISTRY["B.efficacy"].run(s, design, os.path.join(d, "eff"))
            ko = {**design, "perturbation": {**design.get("perturbation", {}), "modality": {"value": "knockout"}}}
            k = qm.REGISTRY["B.efficacy"].run(s, ko, os.path.join(d, "eff-ko"))
        self.assertEqual((r.status, k.status), ("measured", "measured"))
        self.assertIn("is no test of whether a guide acts", r.summary)
        self.assertNotIn("is no test of whether a guide acts", k.summary)
        self.assertTrue(k.biology[0].startswith("Under knockout a guide's own transcript says little"), k.biology)

    def test_a_units_row_says_its_own_change_beside_its_guides(self):
        """A unit's row of the table carries its own place against the line beside the guide's: a unit 24% down of a
        guide past the line over its units does not read as past it (bug register, BR28)."""
        from test_question_modules import synthetic_screen
        with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s, design = synthetic_screen(d)
            r = qm.REGISTRY["B.efficacy"].run(s, design, os.path.join(d, "eff"))
        t = r.table
        self.assertNotIn("verdict", t.columns)
        for _, row in t[t["measurable"]].iterrows():
            words, _ = b.place(row["log2fc"], row["expected"], b.EFFICACY_LOG2FC)
            self.assertIn(row["unit_verdict"], (words, b.TRANSCRIPT_UNCHANGED))
        self.assertEqual(b.place(np.log2(0.76), -1, b.EFFICACY_LOG2FC), ("less than 30% down", False))
        self.assertEqual(b.place(np.log2(0.76), -1, b.EFFICACY_LOG2FC, knockout=True), (b.TRANSCRIPT_UNCHANGED, False))



if __name__ == "__main__":
    unittest.main()
