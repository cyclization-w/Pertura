"""A finding on a cell-cycle phase gets its checks, and a custom finding is told the kind its runs would check
(development session 3, problems 11 and 16)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd


def _service(d, calls):
    from pertura.analysis_service import AnalysisService
    svc = AnalysisService({"workspace": os.path.join(d, "ws")})
    state = {"block_calls": calls, "findings": {}, "cell_sets": {"strict": {"calls": False}}, "runs": {}}
    svc.record = mock.Mock(folder=d)
    svc.record.state.return_value = state
    svc.record.enabled.return_value = True
    svc.record.start_run.return_value = "checks-job"
    svc.record.critic.return_value = []  # nothing open, the research lead not due (docs/adr/0036)
    svc.project = d
    return svc


def _phases(path, barcodes):
    pd.DataFrame({"barcode": barcodes, "S_score": np.linspace(-1, 1, len(barcodes)),
                  "G2M_score": 0.0, "phase": ["G1", "S", "G2M"] * (len(barcodes) // 3)}).to_csv(
        path, sep="\t", index=False, compression="gzip")


class TestPhaseFindings(unittest.TestCase):
    def test_the_checks_read_a_phase_labelling_by_its_phase_column(self):
        from pertura import finding_checks as fc
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "phases.tsv.gz")
            barcodes = [f"c{i}" for i in range(6)]
            _phases(path, barcodes)
            screen = mock.Mock()
            screen.adata.obs_names = barcodes
            spec = fc.load_spec({"kind": "state", "state_table": path}, screen)
            self.assertEqual(list(spec["state_labels"]), ["G1", "S", "G2M", "G1", "S", "G2M"])

    def test_a_composition_call_on_phases_gives_a_state_finding_its_labelling(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "phases.tsv.gz")
            _phases(path, [f"c{i}" for i in range(6)])
            calls = {"cell_cycle-1": {"call_id": "cell_cycle-1", "block": "cell_cycle", "cells": "strict",
                                      "targets": ["T1"], "reference": "control_guides", "kept_cells": True,
                                      "choices": {}, "outputs": {"phases": path}},
                     "composition-1": {"call_id": "composition-1", "block": "composition", "cells": "strict",
                                       "targets": ["T1"], "reference": "control_guides", "kept_cells": True,
                                       "choices": {"of": "cell_cycle-1"}}}
            svc = _service(d, calls)
            try:
                with mock.patch.object(svc, "_screen_dir", return_value=d), \
                        mock.patch.object(svc, "_design_path", return_value="design.yaml"), \
                        mock.patch.object(svc, "_jobs_root", return_value=d):
                    out = svc.record_finding("f1", "C.cell_cycle", "T1", "G1", "up", "all", [], "state",
                                             block_call="composition-1")
                    self.assertEqual(out["checks_run"], "checks-job")
                    cmd = svc.record.start_run.call_args[0][1]
                    with open(cmd[cmd.index("--spec") + 1], encoding="utf-8") as fh:
                        spec = json.load(fh)
                    self.assertEqual(spec["state_table"], path)
            finally:
                svc.record = None
                svc.close()

    def test_a_cell_cycle_run_is_a_state_run(self):
        with tempfile.TemporaryDirectory() as d:
            run = "20261021T161700-C-cell_cycle-abc123"
            folder = os.path.join(d, run, "blocks", "outputs", "cell_cycle-62cab65d")
            os.makedirs(folder)
            _phases(os.path.join(folder, "phases.tsv.gz"), [f"c{i}" for i in range(6)])
            svc = _service(d, {})
            try:
                with mock.patch.object(svc, "_jobs_root", return_value=d):
                    self.assertEqual(svc._run_labelling(run), os.path.join(folder, "phases.tsv.gz"))
                    self.assertIsNone(svc._run_labelling("20261021T000000-C-genes-xyz"))
            finally:
                svc.record = None
                svc.close()

    def test_a_custom_finding_is_told_the_kind_its_runs_would_check(self):
        with tempfile.TemporaryDirectory() as d:
            svc = _service(d, {})
            try:
                with mock.patch.object(svc, "_screen_dir", return_value=d), \
                        mock.patch.object(svc, "_design_path", return_value="design.yaml"), \
                        mock.patch.object(svc, "_jobs_root", return_value=d):
                    out = svc.record_finding("f2", "C.genes", "T1", "a myeloid signature", "up", "all",
                                             ["20261021T154505-C-genes-16900a", "run0043-f3d6b7"], "custom")
                self.assertIn("kinds_with_checks", out)
                self.assertTrue(any("gene_set" in k and "C-genes" in k for k in out["kinds_with_checks"]))
                self.assertIn("no check runs", out["next"])
            finally:
                svc.record = None
                svc.close()


if __name__ == "__main__":
    unittest.main()
