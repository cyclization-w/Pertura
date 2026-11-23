"""ADR 0050, D4: a claim is cheap where the agent works.

On A-cut and the GSE278572 rerun, results rested on the main agent's own notebook descriptions, so no check ran. A
notebook number needed an extra step to carry a claim, and two of the code's own instructions named run_code before
the analysis tools. Now:
- record_finding's cells take the analysis tools' forms ({column: value}, {state, run}), kept as the tools keep them;
- the code's instructions name the analysis tool first;
- record_finding's reply says what a claim's checks cover, so a description shows what it gives up and a claim is not
  checked again by hand (A-cut's main agent redid each guide, each capture, depth and halves in its notebook)."""
import gzip
import os
import unittest
from unittest import mock

from test_results_from_findings import TEXT, Service


class TestACheapClaim(Service):
    def screen_cells(self):
        sd = self.svc._screen_dir()
        os.makedirs(sd, exist_ok=True)
        with gzip.open(os.path.join(sd, "cells.tsv.gz"), "wt", encoding="utf-8") as fh:
            fh.write("barcode\tsample\tunit\tlabel\tcondition\nc1\tS1\tS1\tGATA1\trest\nc2\tS1\tS1\tcontrol\trest\n"
                     "c3\tS1\tS1\tGATA1\tstim\n")

    def test_cells_take_the_forms_the_analysis_tools_take(self):
        self.screen_cells()
        self.ok("record_finding", finding_id="f9", statement="GATA1-i at rest [run:r1].", run_ids=["r1"],
                kind="description", cells={"condition": "rest"})
        st = self.svc.record.state()
        name = st["findings"]["f9"]["cells"]
        self.assertIn("condition_rest", name)
        self.assertIn(name, st["cell_sets"])

    def test_the_reply_says_what_a_claims_checks_cover(self):
        out = self.ok("record_finding", finding_id="f9", statement="a pattern [run:r1].", run_ids=["r1"],
                      kind="description")
        self.assertIn("each guide alone", out["a_claim_gets"])
        parts = {"cells": None, "reference": "control_guides", "kind": "gene", "genes": None, "state_table": None,
                 "lead": None}
        with (mock.patch.object(self.svc, "_finding_parts", return_value=parts),
              mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"})):
            out = self.ok("record_finding", finding_id="f10", target="GATA1", feature="SPI1", direction="up",
                          kind="gene", run_ids=["r1"], statement="GATA1-i raises SPI1 [run:r1].")
        self.assertIn("depth matched", out["its_checks"])
        self.assertIn("by hand", out["its_checks"])

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_the_codes_instructions_name_the_analysis_tool_first(self):
        out = self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                      figure="fig1", text=TEXT)
        weakest = out["check_its_weakest_part"]
        self.assertLess(weakest.index("the analysis tool"), weakest.index("run_code"))
        out = self.ok("open_thread", about="GATA1's own transcript by guide", raised_by="f1", on_line=True)
        do = out["threads_waiting"]["do"]
        self.assertLess(do.index("an analysis tool"), do.index("run_code"))
