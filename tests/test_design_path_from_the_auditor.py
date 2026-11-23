"""What development session 2 (docs/build/development_sessions/session_02_crisprai_haiku) showed on the path from the
auditor's proposal to the screen object, fixed for any screen:
- a unit told apart by a column needs the column, also without biological replicates;
- a key written inside another fact's node is refused;
- a target pooling guides of different modes is refused when references match the mode;
- the vectors flag stays silent for a library that delivers pairs;
- the build stops, with the reason, when the unit column or the delivery's units reach no cell;
- a guessed run ID gets the latest real ones."""
import gzip
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

from pertura import record as rc, screen_object as so
from pertura.analysis_service import AnalysisError, AnalysisService
from pertura.design_audit import checks


def node(v):
    return {"value": v, "status": "proposed", "source": "test"}


def deposit(root, with_metadata=True, metadata_barcodes=None):
    """60 cells in one matrix: A (two guides), B, a control guide; two captures, X and Y, in the cell metadata."""
    os.makedirs(root, exist_ok=True)
    rng = np.random.default_rng(0)
    genes = [f"G{i}" for i in range(50)]
    guides = ["A_1", "A_2", "B_1", "NT_1"]
    n = 60
    X = rng.poisson(3, (n, 50))
    Gm = np.zeros((n, 4), int)
    Gm[:20, 0], Gm[:20, 1] = 4, 3
    Gm[20:40, 2] = 6
    Gm[40:60, 3] = 5
    with gzip.open(os.path.join(root, "s1_matrix.mtx.gz"), "wb") as fh:
        scipy.io.mmwrite(fh, sp.coo_matrix(np.hstack([X, Gm]).T))
    feats = [f"{g}\t{g}\tGene Expression" for g in genes] + [f"{g}\t{g}\tCRISPR Guide Capture" for g in guides]
    open(os.path.join(root, "s1_features.tsv"), "w").write("\n".join(feats) + "\n")
    bcs = [f"C{i:03d}-1" for i in range(n)]
    open(os.path.join(root, "s1_barcodes.tsv"), "w").write("\n".join(bcs) + "\n")
    if with_metadata:
        pd.DataFrame({"cell": metadata_barcodes or bcs, "orig.ident": ["X", "Y"] * 30}).to_csv(
            os.path.join(root, "cell_metadata.csv"), index=False)
    return root


def design(**units):
    return {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                             "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                             "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
            "units": {k: node(v) for k, v in units.items()}}


class TestTheUnitColumn(unittest.TestCase):
    F = {"data.cell_metadata": {"value": {"columns": ["cell", "orig.ident"]}}}

    def problems(self, units):
        out, flags = [], []
        checks._units({"units": {k: node(v) for k, v in units.items()}}, self.F, {}, out, flags)
        return out

    def test_the_column_that_holds_the_units_must_be_the_deposits_without_replicates_too(self):
        """Session 2: the units were to come from a column, none was read, and the build put every cell in unit
        'RNA'. A column the deposit does not hold is refused, naming those it holds."""
        out = self.problems({"independent_unit": "none", "unit_column": "capture"})
        self.assertTrue(any(p.startswith("units.unit_column: no column 'capture'") for p in out), out)
        self.assertIn("orig.ident", " ".join(out))
        self.assertEqual(self.problems({"independent_unit": "none", "unit_column": "orig.ident"}), [])

    def test_a_key_inside_another_facts_node_is_refused(self):
        """Session 2: unit_column was written inside another fact's node and never read."""
        p = {"schema": "pertura.design/v1",
             "units": {"independent_unit": {**node("none"), "unit_column": "capture"}}}
        out = checks.check(p, {})["problems"]
        self.assertTrue(any("units.independent_unit: holds 'unit_column'" in x and "units.unit_column" in x
                            for x in out), out)


class TestModesInOneTarget(unittest.TestCase):
    lib = {"guides": ["G1-a1", "G1-i1", "NTC-a", "NTC-i"]}
    guides = node({"G1-a1": {"mode": "CRISPRa"}, "G1-i1": {"mode": "CRISPRi"}, "NTC-a": {"mode": "CRISPRa"},
                   "NTC-i": {"mode": "CRISPRi"}})
    controls = node([{"name": "NTC", "kind": "non_targeting", "guides": ["NTC-a", "NTC-i"]}])

    def problems(self, target_map, lib=None):
        out = []
        checks._guides({"perturbation": {"controls": self.controls, "modality": node("mixed"), "guides": self.guides,
                                         "reference_matches": node(["mode"]), "target_map": node(target_map)}},
                       lib or self.lib, out)
        return out

    def test_a_target_pooling_both_modes_is_refused(self):
        """Session 2: target_map 'feature_reference' named genes only, so each gene's CRISPRa and CRISPRi guides
        became one target."""
        out = self.problems({"G1": ["G1-a1", "G1-i1"]})
        self.assertTrue(any("1 targets hold guides of different mode" in p and "G1-CRISPRa" in p for p in out), out)
        self.assertEqual(self.problems({"G1-a": ["G1-a1"], "G1-i": ["G1-i1"]}), [])
        pooled = {**self.lib, "reference_labels": {"G1-a1": "G1", "G1-i1": "G1", "NTC-a": "Non-Targeting",
                                                   "NTC-i": "Non-Targeting"}}
        self.assertTrue(any("different mode" in p for p in self.problems("feature_reference", pooled)))


class TestTheVectorsFlag(unittest.TestCase):
    def flags(self, delivery=None):
        problems, flags = [], []
        p = {"perturbation": {"delivery": node(delivery)}} if delivery else {"perturbation": {}}
        shared = np.array([[50.0, 30.0], [30.0, 50.0]])
        with mock.patch.object(checks, "cooccurrence", return_value=(["N1", "N2"], np.array([50.0, 50.0]), shared)):
            checks._vectors(p, {}, problems, flags, {"calls_indptr": 1}, {"N1": "control", "N2": "control"})
        return flags

    def test_guides_sharing_cells_are_flagged_unless_the_library_delivers_pairs(self):
        """Session 2: the flag sent a pair library to perturbation.vectors, whose rules then refused the pairs."""
        self.assertTrue(any("perturbation.vectors" in f["text"] for f in self.flags()))
        self.assertEqual(self.flags({"by_unit": {"D1": "pairs", "S1": "singles"}}), [])


class TestTheBuildStopsWithTheReason(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, d, dep_kw=None):
        dep = deposit(os.path.join(self.tmp.name, "dep"), **(dep_kw or {}))
        return so.build(dep, d, os.path.join(self.tmp.name, "screen"))

    def test_units_from_a_column_of_the_cell_metadata(self):
        s = self.build(design(independent_unit="none", unit_column="orig.ident"))
        self.assertEqual(s["units"], {"X": 30, "Y": 30})

    def test_a_missing_unit_column_stops_the_build(self):
        with self.assertRaises(ValueError) as e:
            self.build(design(independent_unit="none", unit_column="capture"))
        self.assertIn("'capture'", str(e.exception))

    def test_a_unit_column_whose_barcodes_match_no_cell_stops_the_build(self):
        with self.assertRaises(ValueError) as e:
            self.build(design(independent_unit="none", unit_column="orig.ident"),
                       {"metadata_barcodes": [f"Z{i:03d}" for i in range(60)]})
        self.assertIn("'orig.ident'", str(e.exception))  # a table keyed by other barcodes is not this matrix's

    def test_a_delivery_naming_no_unit_stops_the_build(self):
        d = design(independent_unit="none", unit_column="orig.ident")
        d["perturbation"]["delivery"] = node({"by_unit": {"D1": "pairs", "S1": "singles"}})
        with self.assertRaises(ValueError) as e:
            self.build(d)
        self.assertIn("perturbation.delivery names D1, S1", str(e.exception))


class TestAGuessedRunID(unittest.TestCase):
    def test_the_reply_names_the_latest_runs(self):
        """Session 2: three guessed run IDs got 'Pertura failed inside module_result: KeyError'."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = AnalysisService({"workspace": os.path.join(tmp, "ws")})
            svc.record = rc.Record(os.path.join(tmp, "record"))
            svc.record.append("run_logged", {"run_id": "20261020T120000-C-genes-abc123", "what": "module C.genes",
                                             "code": "", "inputs": []}, rc.TOOL)
            jobs = os.path.join(tmp, "jobs")
            os.makedirs(jobs)
            svc._need = lambda: None
            svc._jobs_root = lambda: jobs
            with self.assertRaises(AnalysisError) as e:
                svc.module_result("20261020T120000-C-genes-made-up", wait=60)
            self.assertIn("run IDs come from the tools' replies", str(e.exception))
            self.assertIn("20261020T120000-C-genes-abc123", str(e.exception))


if __name__ == "__main__":
    unittest.main()
