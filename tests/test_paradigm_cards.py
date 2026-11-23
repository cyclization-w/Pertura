"""The paradigm cards (docs/adr/0030; pertura.paradigms): every card in the skill loads, names only registered
modules, and names no blind evaluation screen; the loader refuses a header the kit does not allow; the offer says
which cards the design allows."""
import json
import os
import re
import unittest

from pertura import paradigms
from pertura import question_modules as qm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "plugin", "skills")
# the blind evaluation screens' series, the papers never opened, and the paper kept out of cards (Ranade 2025, the
# paper of a blind evaluation screen), from the one file of sources kept out of the knowledge base (docs/adr/0033, D6)
_KEPT_OUT = os.path.join(ROOT, "analysis", "sources_kept_out_of_the_knowledge_base.json")
_KEPT = json.load(open(_KEPT_OUT, encoding="utf-8")) if os.path.exists(_KEPT_OUT) else None  # the laptop's
BLIND = (_KEPT["series"]["ids"] + _KEPT["never_open"]["ids"] + _KEPT["out_of_cards"]["ids"]
         + _KEPT["out_of_cards"]["names"]) if _KEPT else ()


def card(**over):
    c = {"id": "PX", "version": 1, "updated": "2026-10-22", "name": "a test card", "use_when": "always",
         "leads": "the strongest", "steps": [{"id": "PX.1", "does": "find programs", "part": "module:C.programs",
                                              "fork": "How many?", "fork_default": "the module's"},
                                             {"id": "PX.2", "does": "choose", "part": "notebook", "fork": "Which?",
                                              "fork_key": True}]}
    c.update(over)
    return c


class TestTheCardsInTheSkill(unittest.TestCase):
    def test_every_card_loads_and_names_only_registered_modules(self):
        cards = paradigms.load(modules=set(qm.REGISTRY))
        self.assertIn("P1", cards)
        for c in cards.values():
            self.assertEqual(paradigms.problems(c, set(qm.REGISTRY)), [], c["path"])

    @unittest.skipUnless(_KEPT, "the list of sources kept out stays on the laptop")
    def test_no_skill_file_names_a_blind_screen_or_its_paper(self):
        for folder, _, files in os.walk(SKILL):
            for name in files:
                if name.endswith(".md"):
                    text = open(os.path.join(folder, name), encoding="utf-8").read()
                    found = [b for b in BLIND if re.search(rf"\b{b}\b", text)]
                    self.assertEqual(found, [], os.path.join(folder, name))


class TestTheLoader(unittest.TestCase):
    def test_a_good_card_has_no_problems(self):
        self.assertEqual(paradigms.problems(card(), set(qm.REGISTRY)), [])

    def test_what_the_kit_does_not_allow_is_named(self):
        bad = card(version="one", requires=["a_good_question"], extra=1,
                   steps=[{"id": "PX.1", "does": "x", "part": "module:Z.none"},
                          {"id": "PX.1", "does": "y", "part": "a spreadsheet"},
                          {"id": "PX.3", "does": "z", "part": "notebook", "fork": "?", "fork_key": True,
                           "fork_default": "a"},
                          {"id": "PX.4", "does": "w", "part": "notebook", "fork_default": "b"}],
                   connects=[{"at": "PX.9", "to": "P2"}])
        found = " | ".join(paradigms.problems(bad, set(qm.REGISTRY)))
        for words in ("version is a whole number", "cannot measure", "unknown field extra", "no module Z.none",
                      "step PX.1 twice", "part 'a spreadsheet'", "a key fork is the user's",
                      "belong to a step with a fork", "connects at PX.9"):
            self.assertIn(words, found)



if __name__ == "__main__":
    unittest.main()
