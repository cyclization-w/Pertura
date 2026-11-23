"""Test set v1 (benchmark/test_set_v1): every key is well formed, and the scorer matches findings as README.md says."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TS = os.path.join(os.path.dirname(HERE), "benchmark", "test_set_v1")
if not os.path.isdir(TS):  # the answer keys stay on the laptop: the deploy does not copy them (docs/adr/0009)
    raise unittest.SkipTest("benchmark/ is not here (it is not deployed to the analysis server)")
sys.path.insert(0, TS)
import score_test_set as sts  # noqa: E402

FIELDS = ("id", "target", "feature", "feature_type", "direction", "evidence", "level", "screen_can_show")


class TestTestSet(unittest.TestCase):
    def test_keys_are_well_formed(self):
        tasks = [d for d in sorted(os.listdir(TS)) if os.path.isfile(os.path.join(TS, d, "answer_key.json"))]
        self.assertGreaterEqual(len(tasks), 3)
        for t in tasks:
            self.assertTrue(os.path.isfile(os.path.join(TS, t, "task.md")), t)
            key = json.load(open(os.path.join(TS, t, "answer_key.json"), encoding="utf-8"))
            self.assertIn("written", key)
            ids = [i["id"] for i in key["items"]]
            self.assertEqual(len(ids), len(set(ids)), t)
            for i in key["items"]:
                self.assertEqual([f for f in FIELDS if f not in i], [], (t, i.get("id")))
                self.assertIn(i["level"], ("validated", "screen"))
            task = open(os.path.join(TS, t, "task.md"), encoding="utf-8").read()
            for i in key["items"]:  # the task hints at no answer: no key item's gene feature is named in it
                if i["feature_type"] == "gene":
                    self.assertNotIn(i["feature"], task, (t, i["id"]))

    def test_scoring(self):
        findings = [{"target": "Ikzf1", "feature": "Tcf7", "direction": "up", "feature_type": "gene"},          # Z04
                    {"target": "Ikzf1", "feature": "effector programme (Gzmb, Prf1)", "direction": "up",
                     "feature_type": "programme"},                                                              # Z05 opposite
                    {"target": "Tcf7", "feature": "Ets1", "direction": "same", "feature_type": "similarity"},   # Z20
                    {"target": "Rbpj", "feature": "Tpex share", "direction": "down", "feature_type": "composition"},  # Z11
                    {"target": "Gata3", "feature": "Il4", "direction": "up", "feature_type": "gene"}]           # outside
        r = sts.score(os.path.join(TS, "zhou2023"), findings)
        got = {x["id"]: x for x in r["items"]}
        self.assertTrue(got["Z04"]["recovered"])
        self.assertTrue(got["Z05"]["contradicted"])
        self.assertTrue(got["Z20"]["recovered"])
        self.assertTrue(got["Z11"]["recovered"])
        self.assertFalse(got["Z01"]["recovered"])
        self.assertEqual(r["outside_key"], 1)
        self.assertEqual(r["contradictions"], 1)
        self.assertIn("not_scored", got["Z17"]["gene_set"])


if __name__ == "__main__":
    unittest.main()
