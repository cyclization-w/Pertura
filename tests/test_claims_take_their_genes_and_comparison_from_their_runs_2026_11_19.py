"""A claim takes from its runs, by code, what the recorder's reading of the agent's words left out (2026-11-19).

- A gene-set claim given without genes takes them from its module run's gene lists, or from MSigDB by the set's full
  name: the graded trial on GSE263761 at 0e8bcbb kept all 12 of its claims, on HALLMARK_INTERFERON_ALPHA_RESPONSE,
  without checks, and 56 claims of the runs of 2026-11-08 to 19 lost their checks so.
- A claim whose words name neither its cells nor its reference is checked on the comparison its run made: 28 of the
  848 claims of those runs were checked on every cell against the control guides while their run compared one
  state's cells, a cell set, or every other target's cells. Where its runs compared several ways, it is kept as a
  description, with why, rather than checked on one of them.
"""
import json
import unittest
from unittest import mock

from test_results_from_findings import Service
from test_the_record_kept_by_code_adr0051 import inbox

from pertura import gene_sets, record as rc, recording

PARTS = {"cells": None, "reference": "control_guides", "kind": "gene", "genes": None, "state_table": None,
         "lead": None}


class Claims(Service):
    def setUp(self):
        super().setUp()
        recording._LIBRARY.clear()
        for p in (mock.patch.object(self.svc, "_finding_parts", return_value=PARTS),
                  mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}),
                  mock.patch.object(self.svc, "_confirmed", return_value={})):
            p.start()  # the checks need a built screen object; what the claim takes from its runs is tested here
            self.addCleanup(p.stop)

    def module_run(self, rid, module, given):
        self.svc.record.append("run_logged", {"run_id": rid, "what": f"module {module} with {json.dumps(given)}",
                                              "code": "", "inputs": []}, rc.TOOL)

    def apply(self, tag, claims):
        inbox(self.svc, tag, {"claims": claims,
                              "range": {"session": "s", "agent": "main", "first": tag + "a", "last": tag + "b"}})
        with mock.patch.object(self.svc, "record_finding", wraps=self.svc.record_finding) as rec:
            out = self.ok("pertura_status")["recorded"][0]
        return out, rec

    @staticmethod
    def claim(**more):
        return {"statement": "GATA1 knockdown raises SPI1", "targets": ["GATA1"],
                "feature": {"kind": "gene", "name": "SPI1"}, "direction": "up", "run_ids": ["r1"], **more}


class TestAGeneSetClaimTakesItsGenes(Claims):
    def test_from_the_list_of_its_name_its_module_run_was_given(self):
        self.module_run("r5", "C.programs", {"choices": {"gene_sets": "given"}, "inputs": {"gene_lists": {
            "effector_cytotoxic": ["GZMA", "PRF1", "NKG7"], "progenitor": ["TCF7", "SELL", "CCR7"]}}})
        out, rec = self.apply("t1", [self.claim(feature={"kind": "gene_set", "name": "effector/cytotoxic"},
                                                run_ids=["r5"])])
        self.assertEqual(rec.call_args.kwargs["genes"], ["GZMA", "PRF1", "NKG7"])
        self.assertEqual(out["applied"][0]["genes_from"], "run r5's gene list effector_cytotoxic")
        self.assertEqual(out["claims"]["with_checks"], 1)

    def test_from_msigdb_by_the_sets_full_name(self):
        with mock.patch.object(self.svc, "_organism", return_value="mouse"), \
                mock.patch.object(gene_sets, "msigdb", return_value={
                    "HALLMARK_INTERFERON_ALPHA_RESPONSE": ["Irf7", "Isg15", "Ifit3"]}) as sets:
            out, rec = self.apply("t1", [self.claim(feature={"kind": "gene_set",
                                                             "name": "HALLMARK_INTERFERON_ALPHA_RESPONSE"})])
        sets.assert_called_once_with("hallmark", "mouse")
        self.assertEqual(rec.call_args.kwargs["genes"], ["Irf7", "Isg15", "Ifit3"])
        self.assertEqual(out["applied"][0]["genes_from"],
                         "MSigDB hallmark (mouse): HALLMARK_INTERFERON_ALPHA_RESPONSE")

    def test_from_msigdb_by_the_full_name_its_meaning_run_tested_under_a_short_name(self):
        """GSE203592 on 99eab77: 8 claims on a G.meaning run named their sets in short and kept no genes."""
        import os
        import pandas as pd
        self.module_run("r8", "G.meaning", {"choices": {"collection": "hallmark"}})
        folder = os.path.join(self.svc._jobs_root(), "r8")
        os.makedirs(folder)
        tested = ["HALLMARK_OXIDATIVE_PHOSPHORYLATION", "HALLMARK_TNFA_SIGNALING_VIA_NFKB", "HALLMARK_MYC_TARGETS_V2",
                  "HALLMARK_HEDGEHOG_SIGNALING", "HALLMARK_DNA_REPAIR", "HALLMARK_PEROXISOME"]
        pd.DataFrame({"target": "ARID1A", "set": tested, "q": 0.01}).to_csv(os.path.join(folder, "result.tsv"),
                                                                            sep="\t", index=False)
        names = ["Oxidative phosphorylation (hallmark)", "TNFA_SIGNALING_VIA_NFKB (hallmark)",
                 "MYC_TARGETS_V2 (hallmark)", "DNA repair (hallmark)", "Peroxisome (hallmark)",
                 "Hedgehog signalling (hallmark)", "Hedgehog signalling (hallmark), as I edited it"]
        with mock.patch.object(self.svc, "_organism", return_value="mouse"), \
                mock.patch.object(gene_sets, "msigdb", return_value={s: ["Gene1", "Gene2"] for s in tested}):
            out, rec = self.apply("t1", [self.claim(statement=f"ARID1A knockout raises {n}", targets=["ARID1A"],
                                                    feature={"kind": "gene_set", "name": n}, run_ids=["r8"])
                                         for n in names])
        got = {c.kwargs["feature"]: c.kwargs["genes"] for c in rec.call_args_list}
        self.assertEqual([n for n in names if got[n]], names[:5])  # 'signalling' is not the set's spelling
        self.assertEqual(out["applied"][0]["genes_from"], "MSigDB hallmark (mouse): HALLMARK_OXIDATIVE_PHOSPHORYLATION")

    def test_from_the_records_only_list_of_that_name_another_run_was_given(self):
        """S1 on GSE342764: C.programs defined progenitor_stemlike, and later claims on it rested on notebook runs."""
        self.module_run("r5", "C.programs", {"choices": {"gene_sets": "given"},
                                             "inputs": {"gene_lists": {"progenitor_stemlike": ["TCF7", "SELL"]}}})
        out, rec = self.apply("t1", [self.claim(feature={"kind": "gene_set", "name": "progenitor stemlike"})])
        self.assertEqual(rec.call_args.kwargs["genes"], ["TCF7", "SELL"])
        self.assertIn("the record's only list of that name", out["applied"][0]["genes_from"])

    def test_two_lists_of_that_name_or_lists_of_other_names_give_none(self):
        """None found: the claim goes on without genes, and _finding_parts keeps it a description with what it
        needs (test_nothing_stated_is_refused_whole_revision3 runs that path)."""
        self.module_run("r5", "C.programs", {"choices": {"gene_sets": "given"}, "inputs": {"gene_lists": {
            "a": ["TOX", "CD7", "CD160"], "b": ["TCF7", "SELL", "CCR7"]}}})
        self.module_run("r6", "C.programs", {"choices": {"gene_sets": "given"},
                                             "inputs": {"gene_lists": {"DAM": ["APOE", "CD9"]}}})
        self.module_run("r7", "C.programs", {"choices": {"gene_sets": "given"},
                                             "inputs": {"gene_lists": {"DAM": ["APOE", "TREM2"]}}})
        out, rec = self.apply("t1", [self.claim(feature={"kind": "gene_set", "name": "DAM"}, run_ids=["r5"])])
        self.assertIsNone(rec.call_args_list[0].kwargs["genes"])
        self.assertNotIn("genes_from", out["applied"][0])

    def test_genes_the_agent_named_are_kept_as_they_are(self):
        self.module_run("r5", "C.programs", {"choices": {"gene_sets": "given"},
                                             "inputs": {"gene_lists": {"DAM": ["TOX", "CD7", "CD160"]}}})
        _, rec = self.apply("t1", [self.claim(feature={"kind": "gene_set", "name": "DAM", "genes": ["APOE", "CD9"]},
                                              run_ids=["r5"])])
        self.assertEqual(rec.call_args.kwargs["genes"], ["APOE", "CD9"])


class TestAClaimIsCheckedOnTheComparisonItsRunMade(Claims):
    def test_every_other_targets_cells_as_its_run_compared(self):
        self.module_run("r6", "C.genes", {"choices": {"reference": "other_targets"}})
        out, rec = self.apply("t1", [self.claim(run_ids=["r6"])])
        self.assertEqual((rec.call_args.kwargs["cells"], rec.call_args.kwargs["reference"]), (None, "other_targets"))
        self.assertEqual(out["applied"][0]["comparison_from"], "r6")

    def test_the_cell_set_its_run_was_given(self):
        self.module_run("r6", "C.genes", {"cells": "state_S01-8fb3958f"})
        _, rec = self.apply("t1", [self.claim(run_ids=["r6"])])
        self.assertEqual((rec.call_args.kwargs["cells"], rec.call_args.kwargs["reference"]),
                         ("state_S01-8fb3958f", "control_guides"))

    def test_the_pt_call_on_its_target_gives_its_cells_reference_and_call(self):
        self.svc.record.append("block_called", {"call_id": "genes-1a2b3c4d", "block": "genes", "choices": {},
                                                "cells": "every cell", "targets": ["GATA1", "TAL1"],
                                                "reference": "other_targets", "kept_cells": True, "run_id": "r1",
                                                "notebook": "main"}, rc.TOOL)
        _, rec = self.apply("t1", [self.claim()])
        kw = rec.call_args.kwargs
        self.assertEqual((kw["cells"], kw["reference"], kw["block_call"]), (None, "other_targets", "genes-1a2b3c4d"))

    def test_runs_that_compared_two_ways_leave_it_a_description_with_why(self):
        self.module_run("r6", "C.genes", {})
        self.module_run("r7", "C.genes", {"choices": {"reference": "other_targets"}})
        out, _ = self.apply("t1", [self.claim(run_ids=["r6", "r7"])])
        self.assertEqual(out["claims"]["described"], 1)
        self.assertIn("its runs compared r6 every kept cell against control_guides; r7 every kept cell against "
                      "other_targets", out["applied"][0]["checks"])

    def test_a_run_of_a_module_that_compares_nothing_is_no_comparison(self):
        self.module_run("r6", "B.abundance", {})
        self.module_run("r7", "C.genes", {"cells": "state_S02-dea5702d"})
        _, rec = self.apply("t1", [self.claim(run_ids=["r6", "r7"])])
        self.assertEqual(rec.call_args.kwargs["cells"], "state_S02-dea5702d")

    def test_the_cells_its_words_name_stay_whatever_its_run_compared(self):
        self.module_run("r6", "C.genes", {"choices": {"reference": "other_targets"}})
        with mock.patch.object(self.svc, "_kept_slice", return_value="sample_img"):
            _, rec = self.apply("t1", [self.claim(run_ids=["r6"], cells={"sample": "img"})])
        self.assertEqual((rec.call_args.kwargs["cells"], rec.call_args.kwargs["reference"]),
                         ({"sample": "img"}, None))

    def test_a_default_run_changes_nothing(self):
        self.module_run("r6", "C.genes", {"targets": ["GATA1"]})
        out, rec = self.apply("t1", [self.claim(run_ids=["r6"])])
        self.assertEqual((rec.call_args.kwargs["cells"], rec.call_args.kwargs["reference"],
                          rec.call_args.kwargs["block_call"]), (None, "control_guides", None))
        self.assertEqual(out["claims"]["with_checks"], 1)


if __name__ == "__main__":
    unittest.main()
