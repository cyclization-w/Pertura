"""Layer 2's scripts (analysis/task_evaluation/question_guided): the answers scored against reachable items (matched,
wrong, other; each item matched once; top k), the question's guard against naming a library gene or a listed term,
and the memory probe's prompt (the screen without its data path) and its reading of the model's JSON. Laptop only."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "analysis", "task_evaluation", "question_guided")
if not os.path.isdir(FOLDER):  # the evaluation's scripts stay on the laptop (scripts/deploy_to_ultra.sh)
    raise unittest.SkipTest("the evaluation's scripts stay on the laptop")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(FOLDER, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scorer, guard, probe = (load("score_the_answers_to_the_question"), load("check_the_question_names_no_answer"),
                        load("probe_what_the_model_already_knows"))
TRUTH = {"reachable": [{"targets": ["Tox"], "direction": "lowers"}, {"targets": ["Batf", "Irf4"], "direction": "raises"}]}


def answer(targets, direction):
    return {"targets": targets, "direction": direction, "evidence": "x"}


class TestTheScore(unittest.TestCase):
    def test_matched_wrong_and_other(self):
        got = scorer.score({"answers": [answer(["TOX"], "lowers"), answer(["Irf4"], "lowers"), answer(["Myb"], "raises")]},
                           TRUTH)
        self.assertEqual([a["outcome"] for a in got["answers"]], ["matched", "wrong", "other"])
        self.assertEqual((got["score"], got["recall"]), (0, 0.5))

    def test_an_item_is_matched_once_and_only_the_top_k_count(self):
        twice = {"answers": [answer(["Tox"], "lowers"), answer(["Tox"], "lowers"), answer(["Batf"], "raises")]}
        self.assertEqual(scorer.score(twice, TRUTH)["matched"], 2)
        self.assertEqual(scorer.score(twice, TRUTH, k=1)["matched"], 1)
        self.assertEqual(scorer.score({}, TRUTH)["score"], 0)
        self.assertEqual(scorer.score({"answers": "none"}, TRUTH)["matched"], 0)


class TestTheQuestionsGuard(unittest.TestCase):
    def test_a_library_gene_or_a_listed_term_is_found_as_a_whole_word(self):
        q = "Which of these regulators restrain the effector-like state of exhausted T cells?"
        self.assertEqual(guard.named(q, ["Tox", "Myb"], ["IL-2 signalling"]), {"genes": [], "terms": []})
        self.assertEqual(guard.named("Does tox act through IL-2 signalling?", ["Tox", "Myb"], ["IL-2 signalling"]),
                         {"genes": ["Tox"], "terms": ["IL-2 signalling"]})
        self.assertEqual(guard.named("toxic stress", ["Tox"], [])["genes"], [])  # not inside a word


class TestTheMemoryProbe(unittest.TestCase):
    def test_the_screen_without_its_path_and_the_json_read_from_the_reply(self):
        said = ("I have an in vivo screen in mouse CD8 T cells, at /home/x/trials/deposit/screen_A. Please analyse it: "
                "I want to learn which regulators restrain the effector-like state.")
        self.assertEqual(probe.described(said), "I have an in vivo screen in mouse CD8 T cells. Please analyse it: I "
                                                "want to learn which regulators restrain the effector-like state.")
        self.assertEqual(probe.answers_in('Here: {"answers": [{"targets": ["Tox"]}]} done')["answers"][0]["targets"],
                         ["Tox"])
        self.assertEqual(probe.answers_in("no json"), {})


if __name__ == "__main__":
    unittest.main()
