"""Each cell's hashtag by the field's demultiplexers (pertura.hashtags), written into the cell tables as the column
the design names as the units (docs/adr/0041, step 4a; test_units_are_a_column_of_the_cell_tables.py); no method is
assumed. The checks of the retired design keys units.hashtags and units.hashtag_calls are in
tests_research/test_unit_sources_from_a_fixed_list.py."""
import os
import unittest

import numpy as np
import scipy.sparse as sp

from pertura import design_gate, hashtags

HAS_R = os.path.exists(__import__("pertura.envs", fromlist=["rscript"]).rscript())


def counts(n=300, k=3, seed=0, doublets=20, empty=20):
    """Cells x hashtags: each cell's own hashtag high over a little background; some cells carry two; some none."""
    rng = np.random.default_rng(seed)
    H = rng.poisson(3, size=(n, k)).astype(float)
    own = rng.integers(0, k, n)
    H[np.arange(n), own] += rng.poisson(200, n)
    H[:doublets, (own[:doublets] + 1) % k] += 200
    H[n - empty:] = rng.poisson(1, size=(empty, k))
    return H, own


class TestTheMethods(unittest.TestCase):
    def test_validate_names_what_is_wrong(self):
        self.assertEqual(hashtags.validate({"method": "htodemux", "across_samples": "one unit"}), [])
        self.assertIn("not one of", hashtags.validate({"method": "kmeans"})[0])
        self.assertIn("both numbers", hashtags.validate({"method": "top_ratio"})[0])
        self.assertIn("takes no ratio", hashtags.validate({"method": "htodemux", "settings": {"ratio": 3}})[0])
        self.assertIn("across_samples", hashtags.validate({"method": "multiseqdemux", "across_samples": "x"})[0])
        self.assertEqual(hashtags.validate({"method": "htodemux", "settings": {"positive.quantile": 0.95}}), [])

    def test_top_ratio_only_with_its_numbers(self):
        H, own = counts()
        tag, call, info = hashtags.demultiplex(sp.csr_matrix(H), ["A", "B", "C"],
                                               value={"method": "top_ratio", "min_counts": 5, "ratio": 3})
        self.assertEqual(info["calls"]["several"], 20)
        self.assertEqual(info["calls"]["none"], 20)
        clear = call == "one"
        self.assertTrue((tag[clear] == np.array(["A", "B", "C"])[own[clear]]).all())

    def test_describe_and_shares(self):
        self.assertIn("HTODemux at its defaults", hashtags.describe({"method": "htodemux"}))
        self.assertIn("one hashtag marks the same sample in every capture",
                      hashtags.describe({"method": "multiseqdemux", "across_samples": "one unit"}))
        # a hashtag marks a sample, which can be one condition's cells: the words never say it is an animal
        self.assertNotIn("animal", hashtags.describe({"method": "htodemux", "across_samples": "one unit"}))
        shares = hashtags.shares_by_sample(["A", "A", "B", "", "A", "B"], ["s1", "s1", "s1", "s1", "s2", "s2"])
        self.assertEqual(shares, {"s1": {"A": 0.667, "B": 0.333}, "s2": {"A": 0.5, "B": 0.5}})

    @unittest.skipUnless(HAS_R, "needs R with Seurat")
    def test_the_fields_demultiplexers_one_sample_at_a_time(self):
        H, own = counts(n=600, seed=1)
        groups = ["s1"] * 300 + ["s2"] * 300
        import tempfile
        work = os.path.join(tempfile.mkdtemp(), "not", "made", "yet")  # a deposit of h5 matrices parses nothing first
        for method in ("htodemux", "multiseqdemux"):
            tag, call, info = hashtags.demultiplex(sp.csr_matrix(H), ["A", "B", "C"], groups=groups,
                                                   value={"method": method}, workdir=work)
            self.assertEqual(info["groups_not_called"], {}, method)
            clear = call == "one"
            self.assertGreater(clear.mean(), 0.7, method)
            self.assertGreater((tag[clear] == np.array(["A", "B", "C"])[own[clear]]).mean(), 0.98, method)


class TestTheUnitsConfirmed(unittest.TestCase):
    def test_a_confirmation_of_the_units_covers_the_unit_and_its_column(self):
        def proposal(**units):
            return {"units": {k: {"value": v, "status": "proposed", "source": "test"} for k, v in units.items()}}
        covered = design_gate.confirmed_value(proposal(independent_unit="mouse", unit_column="hashtag"),
                                              "units.independent_unit")
        self.assertEqual(covered, {"independent_unit": "mouse", "unit_column": "hashtag"})


if __name__ == "__main__":
    unittest.main()
