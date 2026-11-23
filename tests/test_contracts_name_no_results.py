"""The contracts say what each module or block was checked on and how, without the results of the real screens they
name (development session 3, problem 10: list_modules told the agent CRISPRai's own results)."""
import re
import unittest

SCREENS = ("CRISPRai", "zebrafish", "ILF3", "CAR-T", "Cas12a", "macrophage", "learning screens", "Zhou", "Replogle")
NUMBER = re.compile(r"(?<![A-Za-z0-9_-])\d")  # a count, a share or a statistic; not a digit inside a name (CD8, Cas12a)


class TestContractsNameNoResults(unittest.TestCase):
    def test_no_contract_naming_a_real_screen_carries_a_number(self):
        from pertura import blocks
        from pertura.question_modules import REGISTRY
        texts = {n: m.contract.tested_against for n, m in REGISTRY.items()}
        texts.update({f"pt.{n}": b.contract.tested_against for n, b in blocks.BLOCKS.items()})
        bad = {n: t for n, t in texts.items() if any(s in (t or "") for s in SCREENS) and NUMBER.search(t)}
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
