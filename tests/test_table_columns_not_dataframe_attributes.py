"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 14), fixed for
the whole class (docs/build_goal_session6_fixes.md, item 18.2): a module's table had a column named `mode`, and the
agent's `df.mode` gave pandas' method, not the column. No column of a module's table is named after an attribute of a
pandas DataFrame."""
import os
import tempfile
import unittest
import warnings

import numpy as np
import pandas as pd

from pertura import question_modules as qm
from pertura.steps import effects_across_targets as eat

ATTRIBUTES = {a for a in dir(pd.DataFrame) if not a.startswith("_")}


def clashes(table):
    return sorted(str(c) for c in getattr(table, "columns", []) if str(c) in ATTRIBUTES)


class TestTheModulesTables(unittest.TestCase):
    def test_every_module_that_runs_on_the_synthetic_screen(self):
        from test_question_modules import synthetic_screen
        ran, found = 0, {}
        with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s, design = synthetic_screen(d)
            for name, m in qm.REGISTRY.items():
                try:
                    r = m.run(s, design, os.path.join(d, name.replace(".", "_")))
                except Exception:  # a module that needs R, a package or inputs; the server's run covers it
                    continue
                ran += 1
                for tname, t in {"result": r.table, **(r.tables or {})}.items():
                    if clashes(t):
                        found[f"{name}:{tname}"] = clashes(t)
        self.assertGreater(ran, 5)
        self.assertEqual(found, {})

    def test_the_programs_correlated_over_the_targets(self):
        """E.program_axis' table (pertura.steps.effects_across_targets.correlate), by each target's mode."""
        rng = np.random.default_rng(0)
        rows = [{"target": f"T{i}", "score": p, "effect": float(rng.normal())}
                for i in range(8) for p in ("P1", "P2", "P3")]
        modes = {f"T{i}": ("CRISPRa" if i % 2 else "CRISPRi") for i in range(8)}
        df = eat.correlate(pd.DataFrame(rows), modes, "pearson", {}, min_targets=3)
        self.assertIn("guide_mode", df.columns)
        self.assertEqual(clashes(df), [])


if __name__ == "__main__":
    unittest.main()
