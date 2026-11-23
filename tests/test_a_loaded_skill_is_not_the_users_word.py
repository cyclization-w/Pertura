"""The Stop hook's "the user needs the turn" reads the user's own last message. Dev run 2 of loop v2 (GSE342764,
cffc67d) ended with nothing checked: Claude Code puts a loaded skill's text in the transcript as a user message
(isMeta), the Pertura skill says wait_for_runs and stop, and the hook took them as the user's words to stop. A word
to stop now counts only as a whole word in a short message, and meta messages are not the user's."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import stop_words  # noqa: E402

TASK = "Write your answers to /x/answers.json as one JSON object with the four sections below. " * 4


class TheUsersOwnWords(unittest.TestCase):
    def test_a_loaded_skill_is_skipped(self):
        entries = [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": TASK}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Loading the skill."}]}},
            {"type": "user", "uuid": "u2", "isMeta": True, "message": {"role": "user", "content": [
                {"type": "text", "text": "Base directory for this skill: x\n\nAfter starting runs, arm one "
                                         "wait_for_runs ... it is no reason to stop."}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "The answers are written."}]}},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(e) for e in entries) + "\n")
            reply, asked = stop_words.last_turn({"transcript_path": path})
        self.assertEqual(asked, TASK)
        self.assertFalse(stop_words.user_needs_the_turn(asked))

    def test_a_word_to_stop_is_a_whole_word_in_a_short_message(self):
        needs = stop_words.user_needs_the_turn
        self.assertTrue(needs("Please wait, I'll check the guides."))
        self.assertTrue(needs("停一下"))
        self.assertTrue(needs("Stop."))
        self.assertTrue(needs("Which targets change Tox?"))
        self.assertFalse(needs("arm one wait_for_runs and work on"))
        self.assertFalse(needs(TASK + " Do not stop before every section is written."))


if __name__ == "__main__":
    unittest.main()
