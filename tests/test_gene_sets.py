"""pertura.gene_sets on a small fake folder laid out like the fetched set (unittest, so it runs on the analysis
server, which has no pytest)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from pertura import gene_sets as gs


class TestGeneSets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for d in ("msigdb", "collectri", "progeny"):
            (root / d).mkdir()
        (root / "msigdb" / "mh.all.v2025.1.Mm.symbols.gmt").write_text(
            "HALLMARK_A" + chr(9) + "url" + chr(9) + "Cd8a" + chr(9) + "Gzmb" + chr(10)
            + "HALLMARK_B" + chr(9) + "url" + chr(9) + "Tcf7" + chr(10))
        pd.DataFrame({"source": ["Tcf7", "Tcf7"], "target": ["Sell", "Gzmb"], "weight": [1, -1],
                      "resources": ["x", "x"]}).to_csv(root / "collectri" / "CollecTRI_regulons_mouse.tsv",
                                                       sep=chr(9), index=False)
        pd.DataFrame({"gene": ["A", "B", "C"], "pathway": ["MAPK", "MAPK", "p53"], "weight": [1.0, 2.0, 3.0],
                      "p.value": [0.01, 0.2, 0.03]}).to_csv(root / "progeny" / "progeny_mouse_full.tsv", sep=chr(9),
                                                            index=False)
        (root / "MANIFEST.json").write_text(json.dumps({"files": {}}))
        self.env = mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": str(root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_loaders(self):
        self.assertEqual(gs.msigdb("hallmark", "mouse"), {"HALLMARK_A": ["Cd8a", "Gzmb"], "HALLMARK_B": ["Tcf7"]})
        self.assertEqual(list(gs.collectri("mouse").columns), ["source", "target", "weight"])
        p = gs.progeny("mouse", top=1)
        self.assertEqual(sorted(p["target"]), ["A", "C"])
        self.assertEqual(list(p.columns), ["source", "target", "weight", "p_value"])
        self.assertEqual(gs.manifest(), {"files": {}})
        with self.assertRaises(ValueError):
            gs.msigdb("kegg", "mouse")  # no mouse KEGG, and no human file in this folder to translate

    def test_a_human_only_collection_reaches_mouse_through_hcop(self):
        """Mouse M7 holds no exhaustion set and human C7 holds 18: a mouse screen gets the human sets translated
        through HCOP, as decoupler.op.translate does it (at least 3 resources, at most 5 orthologs a gene)."""
        root = Path(self.tmp.name)
        (root / "msigdb" / "c7.immunesigdb.v2025.1.Hs.symbols.gmt").write_text(
            "EXHAUSTED_UP" + chr(9) + "url" + chr(9) + "PDCD1" + chr(9) + "TOX" + chr(9) + "WEAK" + chr(9) + "MANY"
            + chr(10))
        (root / "hcop").mkdir()
        rows = [("PDCD1", "Pdcd1", "A,B,C"), ("TOX", "Tox", "A,B,C,D"), ("WEAK", "Weak", "A,B")]
        rows += [("MANY", f"Many{i}", "A,B,C") for i in range(6)]
        pd.DataFrame(rows, columns=["human_symbol", "mouse_symbol", "support"]).to_csv(
            root / "hcop" / "human_mouse_hcop_fifteen_column.txt.gz", sep=chr(9), index=False, compression="gzip")
        self.assertTrue(gs.translated("immunesigdb", "mouse"))
        self.assertEqual(gs.msigdb("immunesigdb", "mouse"), {"EXHAUSTED_UP": ["Pdcd1", "Tox"]})
        self.assertEqual(gs.msigdb("c7.immunesigdb", "mouse"), {"EXHAUSTED_UP": ["Pdcd1", "Tox"]})
        self.assertFalse(gs.translated("hallmark", "mouse"))  # MSigDB's own mouse sets stay as they are


if __name__ == "__main__":
    unittest.main()
