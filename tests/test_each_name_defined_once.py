"""Each module of the library defines a top-level function or class once: a second definition of a name silently
takes the first's place, and the callers of the first then get the second. Read from the source, so it runs without
the analysis packages."""
import ast
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


class TestEachNameDefinedOnce(unittest.TestCase):
    def test_no_module_defines_a_top_level_name_twice(self):
        twice = []
        for path in sorted(SRC.rglob("*.py")):
            seen = {}
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in seen:
                        twice.append(f"{path.relative_to(SRC)}: {node.name} (lines {seen[node.name]} and "
                                     f"{node.lineno})")
                    seen.setdefault(node.name, node.lineno)
        self.assertEqual(twice, [])


if __name__ == "__main__":
    unittest.main()
