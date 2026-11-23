"""ADR 0051, D9: published screens and complexes as outside references. For a target, Replogle et al. 2022's CRISPRi
screens give its knockdown and cells there and the perturbations whose profiles are nearest (Pearson); CORUM gives the
complexes that hold it. Read as prior knowledge, recorded as such."""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from test_results_from_findings import Service

from pertura import contracts, prior_screens as ps


def write_reference(folder, name):
    import anndata as ad
    genes = [f"G{i}" for i in range(8)]
    rng = np.random.default_rng(0)
    base = rng.normal(size=8)
    x = np.vstack([base, base * 0.9 + 0.01, -base, rng.normal(size=8)]).astype(np.float32)
    obs = pd.DataFrame({"fold_expr": [0.2, 0.3, 0.9, 0.5], "num_cells_filtered": [150, 120, 80, 60]},
                       index=["1_AAA_P1P2_ENSG1", "2_BBB_P1P2_ENSG2", "3_CCC_P1_ENSG3", "4_DDD_P1_ENSG4"])
    var = pd.DataFrame({"gene_name": genes}, index=[f"ENSG{100 + i}" for i in range(8)])
    ad.AnnData(X=x, obs=obs, var=var).write_h5ad(os.path.join(folder, ps.REFERENCES[name]))


class TestTheReferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_reference(self.tmp, "K562_essential")
        pd.DataFrame({"complex_name": ["AAA-BBB complex", "Mouse only"], "organism": ["Human", "Mouse"],
                      "subunits_gene_name": ["AAA;BBB", "Aaa;Eee"]}).to_csv(
            os.path.join(self.tmp, "corum_allComplexes.txt"), sep="\t", index=False)
        for p in (mock.patch.object(ps, "FOLDER", self.tmp), mock.patch.object(ps, "CORUM", self.tmp),
                  mock.patch.dict(ps._cache, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_held_target_gives_its_knockdown_its_nearest_and_its_complexes(self):
        rows = ps.look_up(["AAA", "ZZZ"], ["K562_essential"], k=2)
        aaa, zzz = rows
        self.assertTrue(aaa["in_reference"])
        self.assertEqual((aaa["knockdown_fold"], aaa["cells"]), (0.2, 150))
        self.assertEqual(aaa["nearest"][0]["target"], "BBB")  # the profile that moves with it
        self.assertGreater(aaa["nearest"][0]["r"], 0.99)
        self.assertEqual(aaa["complexes"], ["AAA-BBB complex"])
        self.assertFalse(zzz["in_reference"])
        self.assertNotIn("nearest", zzz)

    def test_the_contract_bounds_the_call(self):
        self.assertIsNotNone(contracts.checked("prior_screens", {"targets": []})[1])
        self.assertIsNotNone(contracts.checked("prior_screens", {"targets": ["A"], "references": ["HeLa"]})[1])
        self.assertIsNone(contracts.checked("prior_screens", {"targets": ["A"], "neighbours": 3})[1])


class TestTheTool(Service):
    def test_the_look_up_is_recorded_as_prior_knowledge(self):
        rows = [{"target": "GATA1", "reference": "K562_essential", "in_reference": True, "complexes": []}]
        with (mock.patch.object(ps, "look_up", return_value=rows) as look,
              mock.patch.object(self.svc, "_organism", return_value="human")):  # the design's organism, as gene_sets
            out = self.ok("prior_screens", targets=["GATA1"])
        self.assertEqual(out["rows"], rows)
        self.assertEqual(look.call_args.args[0], ["GATA1"])
        known = self.svc.record.state()["knowledge"][-1]
        self.assertIn("Replogle", known["source"])


if __name__ == "__main__":
    unittest.main()
