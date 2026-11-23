"""pt.enrich reads a C.genes run as its own pt.genes call, so the universe is every gene tested (development session
3, problem 23: the agent held C.genes' changed genes and could not give them to pt.enrich)."""
import json
import os
import tempfile
import unittest

import pandas as pd


class TestEnrichOfAModuleRun(unittest.TestCase):
    def test_a_run_and_its_tables_id_both_give_the_main_calls_full_table(self):
        from pertura import blocks
        with tempfile.TemporaryDirectory() as d:
            run = "20261021T154505-C-genes-16900a"
            out = os.path.join(d, run, "blocks", "outputs")
            full = pd.DataFrame({"target": ["T"] * 4, "gene": list("abcd"), "effect": [1.0, -1.0, 0.1, 0.0],
                                 "q": [0.001, 0.002, 0.5, 0.9]})
            calib = pd.DataFrame({"target": ["ncg_1"], "gene": ["a"], "effect": [0.1], "q": [0.5]})
            for cid, table in (("genes-main", full), ("genes-calib", calib)):
                os.makedirs(os.path.join(out, cid))
                table.to_csv(os.path.join(out, cid, "table.tsv.gz"), sep="\t", index=False, compression="gzip")
            with open(os.path.join(d, run, "blocks", blocks.LOG), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"call_id": "genes-calib", "block": "genes", "check_of": "genes-main"}) + "\n")
                fh.write(json.dumps({"call_id": "genes-main", "block": "genes", "check_of": None}) + "\n")
            before = dict(blocks.CONTEXT)
            blocks.CONTEXT["jobs_dir"] = d
            try:
                for ref in (run, f"{run}:changed_genes"):
                    got = blocks._genes_table(ref)
                    self.assertEqual(sorted(got["gene"]), list("abcd"), ref)  # all four tested, not the changed two
            finally:
                blocks.CONTEXT.clear()
                blocks.CONTEXT.update(before)


if __name__ == "__main__":
    unittest.main()
