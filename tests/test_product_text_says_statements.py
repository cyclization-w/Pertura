"""The product's text says statements: no clause in place of the content it points at, such
as "where it stands" or "what it shows", and no heading or bold label that opens with a question word. The agent
copies the text it reads (its replies opened with "**Where it stands:**" and "**What happened since:**", as /catchup
listed them), so the plugin's text and the strings the code gives the agent and the user are checked; code comments
and docstrings are not. The reply style's own counter-examples are exempt."""
import glob
import io
import os
import re
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED = re.compile(r"\bwhere (it|the analysis|things|the screen's analysis) stands?\b|\bwhat (it|the figure) shows\b|"
                    r"\bwhat has been found\b|\bwhat (may )?comes? next\b|\bwhat happened since\b|"
                    r"\bwhat (it |they )?waits? on\b", re.I)
LABEL = re.compile(r"(^|\n)#+ (What|Where|How|Why|Which|Whether)\b|\*\*(What|Where|How|Why|Which|Whether)\b")
EXEMPT = "- **Statements, not clauses.**"  # the reply style's rule, whose counter-examples are clauses


def markdown_lines():
    for path in glob.glob(os.path.join(ROOT, "plugin", "**", "*.md"), recursive=True):
        rule = False
        for n, line in enumerate(open(path, encoding="utf-8").read().splitlines(), 1):
            rule = line.startswith(EXEMPT) or (rule and line.startswith("  "))
            if not rule:
                yield os.path.relpath(path, ROOT), n, line


def code_strings():
    paths = glob.glob(os.path.join(ROOT, "plugin", "**", "*.py"), recursive=True) + \
        glob.glob(os.path.join(ROOT, "src", "pertura", "**", "*.py"), recursive=True)
    for path in paths:
        tokens = list(tokenize.generate_tokens(io.StringIO(open(path, encoding="utf-8").read()).readline))
        for k, t in enumerate(tokens):
            if t.type != tokenize.STRING:
                continue
            before = [x for x in tokens[max(0, k - 3):k]
                      if x.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT)]
            if t.string.lstrip("rbfuRBFU").startswith(('"""', "'''")) and (not before or before[-1].string == ":"):
                continue  # a docstring: for whoever reads the code
            yield os.path.relpath(path, ROOT), t.start[0], t.string


class TestStatements(unittest.TestCase):
    def test_the_plugins_text_says_statements(self):
        found = [f"{p}:{n}: {line.strip()[:100]}" for p, n, line in markdown_lines()
                 if BANNED.search(line) or LABEL.search(line)]
        self.assertEqual(found, [])

    def test_the_strings_the_code_gives_say_statements(self):
        found = [f"{p}:{n}: {s.strip()[:100]}" for p, n, s in code_strings() if BANNED.search(s) or LABEL.search(s)]
        self.assertEqual(found, [])

    def test_the_check_catches_a_clause_and_a_label(self):
        self.assertTrue(BANNED.search("the overview: where the analysis stands"))
        self.assertTrue(LABEL.search("- **What happened since:** two workers ended"))
        self.assertFalse(BANNED.search("The research lead recorded complete.") or LABEL.search("**Next question.**"))


if __name__ == "__main__":
    unittest.main()
