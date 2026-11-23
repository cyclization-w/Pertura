"""What development session 6 showed (problem 14), fixed for any session (docs/build_goal_session6_fixes.md, item
18.3): the output style fixed the status block's labels and phrases in English, so every Chinese reply carried English
lines. The block follows the chat's language, its labels too."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheBlockFollowsTheChat(unittest.TestCase):
    def test_the_output_style_says_so(self):
        with open(os.path.join(ROOT, "plugin", "output-styles", "pertura.md"), encoding="utf-8") as fh:
            style = " ".join(fh.read().split())
        self.assertIn("written in the language of the user's messages", style)
        self.assertIn("阶段 · 已发现 · 下一步 · 在等", style)
        self.assertIn("never pasted in English", style)
        self.assertNotIn("Plain English", style)


if __name__ == "__main__":
    unittest.main()
