"""What a result was read from holds while the session goes on (the open points of 2026-11-19).

- An open notebook reads the screen object again when the confirmed cells' rule changes, as it does after a rebuild:
  the rule is confirmed after the build and the notebook does not wait for it, so a kernel that read the object first
  kept the cells of the rule before.
- A module run or a finding's checks made under another cells' rule are not given back as the same comparison: the
  rule is left out of the build's ID, so a tightened rule left the earlier runs and checks in reuse.
- Opening another project shuts the notebooks of the one before: a kernel held the other project's names and wrote
  its tables, cell sets and figures in that project's folder.
- A score finding keeps the version of the table it read: pt.save_table writes over a table saved under the same
  name, and the checks took the latest table of that name in any notebook.
"""
import gzip
import json
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import make_deposit
from test_results_from_findings import Service

import pertura.blocks as pt
from pertura import record as rc


class TestAnOpenNotebookReadsTheObjectAgainWhenTheCellsRuleChanges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen_dir = os.path.join(self.tmp.name, "screen")
        os.makedirs(self.screen_dir)
        with open(os.path.join(self.screen_dir, "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"build_id": "b1"}, fh)
        self.design = os.path.join(self.tmp.name, "design.yaml")
        self.write_rule(500)
        pt.CONTEXT.clear()
        pt.context(screen_dir=self.screen_dir, design_path=self.design, notebook_dir=self.tmp.name)
        pt._SCREEN.clear()
        pt._BUILD.clear()
        self.made = []
        patch = mock.patch.object(pt, "Screen", side_effect=lambda d, design: self.made.append(design) or object())
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        pt.CONTEXT.clear()
        pt._SCREEN.clear()
        pt._BUILD.clear()
        self.tmp.cleanup()

    def write_rule(self, least, when=None):
        with open(self.design, "w", encoding="utf-8") as fh:
            fh.write("data:\n  qc:\n    value:\n      filters:\n      - metric: n_genes\n"
                     f"        min: {least}\n    status: confirmed\n")
        if when is not None:  # a later write, as the form's answer gives it
            os.utime(self.design, (when, when))

    def test_a_changed_rule_reads_it_again_and_the_same_rule_keeps_it(self):
        first = pt.screen()
        self.assertIs(pt.screen(), first)
        self.assertEqual(len(self.made), 1)
        t = os.path.getmtime(self.design)
        self.write_rule(500, when=t + 10)  # written again, the same rule: the object stays
        self.assertIs(pt.screen(), first)
        self.write_rule(1000, when=t + 20)  # a tighter rule: read again, with it
        second = pt.screen()
        self.assertIsNot(second, first)
        self.assertEqual(self.made[-1]["data"]["qc"]["value"]["filters"][0]["min"], 1000)
        self.assertIs(pt.screen(), second)

    def test_a_rebuild_still_reads_it_again(self):
        first = pt.screen()
        with open(os.path.join(self.screen_dir, "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"build_id": "b2"}, fh)
        self.assertIsNot(pt.screen(), first)


class TestWhatIsGivenAgainIsUnderTheSameRule(Service):
    def test_a_module_run_under_another_cells_rule_is_another_comparison(self):
        self.svc.record.append("run_logged", {"run_id": "m1", "job_id": "m1", "what": "module C.genes", "code": "",
                                              "inputs": [], "cells_rule": "aaa"}, rc.TOOL)
        with mock.patch.object(self.svc, "_cells_rule_id", return_value="aaa"):
            self.assertEqual(self.svc._same_comparison("C.genes", {}), "m1")
        with mock.patch.object(self.svc, "_cells_rule_id", return_value="bbb"):
            self.assertIsNone(self.svc._same_comparison("C.genes", {}))
        self.svc.record.append("run_logged", {"run_id": "m0", "job_id": "m0", "what": "module C.programs",
                                              "code": "", "inputs": []}, rc.TOOL)  # logged before runs recorded it
        with mock.patch.object(self.svc, "_cells_rule_id", return_value="bbb"):
            self.assertEqual(self.svc._same_comparison("C.programs", {}), "m0")

    def test_a_run_records_the_rule_it_reads(self):
        self.assertEqual(self.svc.record.rule_of, self.svc._cells_rule_id)  # wired when the project opens
        with mock.patch.object(self.svc.record, "rule_of", lambda: "aaa"), \
                mock.patch("pertura.runner.start", return_value="m2"):
            self.svc.record.start_run("module C.genes", ["true"], [], self.tmp.name)
        self.assertEqual(self.svc.record.state()["runs"]["m2"]["cells_rule"], "aaa")

    def test_the_rule_is_read_from_the_design_as_screen_applies_it(self):
        with mock.patch.object(self.svc, "_design_path", return_value=os.path.join(self.tmp.name, "none.yaml")):
            self.assertIsNone(self.svc._cells_rule_id())
        path = os.path.join(self.tmp.name, "d.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("data:\n  qc:\n    value: {filters: [{metric: n_genes, min: 500}]}\n")
        with mock.patch.object(self.svc, "_design_path", return_value=path):
            one = self.svc._cells_rule_id()
        self.assertRegex(one, r"^[0-9a-f]{12}$")

    def test_checks_under_another_cells_rule_are_another_comparison(self):
        folder = os.path.join(self.tmp.name, "specs")
        os.makedirs(folder)
        keys = []
        for i, rule in enumerate(("aaa", "aaa", "bbb")):
            path = os.path.join(folder, f"f-{i}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"target": "GATA1", "kind": "gene", "feature": "SPI1",
                           "_queue": {"finding_id": f"f{i}", "build": "b1", "cells_rule": rule}}, fh)
            keys.append(self.svc._spec_key(path)[0])
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], keys[2])


class TestAnotherProjectShutsTheNotebooksOfTheOneBefore(Service):
    def test_another_project_shuts_them_and_the_same_project_keeps_them(self):
        class Kernel:
            shut = False

            def shutdown(self):
                self.shut = True

        kernel = Kernel()
        self.svc.notebooks["main"], self.svc.flows["main"] = kernel, (1, None)
        self.ok("open_project", data_folder=os.path.join(self.tmp.name, "deposit"))
        self.assertIs(self.svc.notebooks["main"], kernel)  # the same project: the kernel and its names stay
        other = os.path.join(self.tmp.name, "other_deposit")
        os.makedirs(other)
        make_deposit(other)
        self.ok("open_project", data_folder=other)
        self.assertTrue(kernel.shut)
        self.assertEqual((self.svc.notebooks, self.svc.flows), ({}, {}))


class TestAScoreFindingKeepsTheVersionOfTheTableItRead(Service):
    def save(self, notebook, rows):
        folder = os.path.join(self.svc.project, "notebooks", notebook, "tables")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "program.tsv.gz")
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write("barcode\tscore\n" + "".join(f"c{i}\t{v}\n" for i, v in enumerate(rows)))
        return path

    def test_a_copy_named_by_its_content_is_read_whatever_is_saved_later(self):
        self.save("main", [1.0, 2.0, 3.0])
        bound = self.svc._score_table("table:program")
        self.assertTrue(bound.startswith(os.path.join(self.svc.project, "checks", "score_tables", "program-")))
        later = self.save("t2-probe", [-1.0, -2.0, -3.0])  # another notebook saves its own under the name
        os.utime(later, (os.path.getmtime(later) + 10,) * 2)
        self.assertEqual(self.svc._score_table("table:program", bound=bound), bound)
        with gzip.open(bound, "rt", encoding="utf-8") as fh:
            self.assertIn("c0\t1.0", fh.read())
        self.assertNotEqual(self.svc._score_table("table:program"), bound)  # unbound: the latest, as before

    def test_the_finding_holds_its_copy_and_its_checks_read_it(self):
        parts = {"kind": "score", "cells": None, "reference": "control_guides", "lead": None, "state_table": None,
                 "genes": None, "score_table": "/the/copy.tsv.gz"}
        with mock.patch.object(self.svc, "_finding_parts", return_value=parts) as fp, \
                mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}):
            self.ok("record_finding", finding_id="s1", target="GATA1", feature="table:program", direction="up",
                    kind="score", run_ids=["r1"], statement="GATA1 raises the program")
            self.assertEqual(self.svc.record.state()["findings"]["s1"]["score_table"], "/the/copy.tsv.gz")
            self.svc.check_finding("s1")
        self.assertEqual(fp.call_args.kwargs["score_table"], "/the/copy.tsv.gz")


if __name__ == "__main__":
    unittest.main()
