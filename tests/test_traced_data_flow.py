"""ADR 0027, a traced data flow without gates:
- the confinement's audit hook writes the files code opens under its own folders, read and written, and not the
  Python installation's or an untraced folder's;
- names are followed between cells: what a cell read stands behind the names it assigned, and a later cell that
  reads them uses it; an imported module is not followed;
- every object in one view, and a result's chain from the deposit and the design, in order, with Pertura's steps and
  the agent's told apart, as the report's methods give it;
- a module takes a table by its object ID; a table without a column the module reads, and an ID the record does not
  hold, are named;
- a dossier part with a sentence that names no entry is kept, and the sentence is listed;
- end to end in a notebook: a cell that reads the screen object and one that saves a table have their inputs and
  outputs in the record, with nothing declared."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

import pandas as pd

from pertura import flow, record as rc, results_report
from pertura.analysis_service import AnalysisError, AnalysisService

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

try:
    import ipykernel  # noqa: F401
    import jupyter_client  # noqa: F401
    HAVE_KERNEL = True
except ImportError:
    HAVE_KERNEL = False


class TestTheHookTracesFiles(unittest.TestCase):
    def test_reads_and_writes_under_the_callers_folders(self):
        with tempfile.TemporaryDirectory() as d:
            view, work = os.path.join(d, "view"), os.path.join(d, "work")
            cache = os.path.join(work, "tmp")
            for f in (view, cache):
                os.makedirs(f)
            with open(os.path.join(view, "a.tsv"), "w") as fh:
                fh.write("x\n1\n")
            trace = os.path.join(d, "files.tsv")
            code = textwrap.dedent(f"""
                import os, sys
                sys.path.insert(0, {SRC!r})
                from pertura import confine
                confine.install(read=[{view!r}], write=[{work!r}], trace={trace!r}, untraced=[{cache!r}])
                open(os.path.join({view!r}, "a.tsv")).read()
                open(os.path.join({work!r}, "b.tsv"), "w").write("y")
                open(os.path.join({cache!r}, "c.tmp"), "w").write("z")
                import decimal, fractions
                print("done")
                """)
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
            self.assertIn("done", out.stdout, out.stderr)
            with open(trace, encoding="utf-8") as fh:
                lines = [ln.split("\t") for ln in fh.read().splitlines()]
            seen = {(k, os.path.basename(p)) for k, p in lines}
            self.assertEqual(seen, {("R", "a.tsv"), ("W", "b.tsv")})  # not the cache, not the installation


class TestNamesBetweenCells(unittest.TestCase):
    def test_what_a_cell_read_stands_behind_its_names(self):
        k = flow.Kernel()
        self.assertEqual(k.cell("import numpy as np\nt = pd.read_csv(path)", ["run1:changed_genes"], []),
                         ["run1:changed_genes"])
        self.assertEqual(k.cell("fit = t.groupby('target').size()\nrows = []\nrows.append(fit)", [], []),
                         ["run1:changed_genes"])
        self.assertEqual(k.cell("np.mean(rows)", [], []), ["run1:changed_genes"])  # rows, not the module np
        self.assertEqual(k.cell("x = 5", [], []), [])
        self.assertEqual(k.cell("print(x)", [], []), [])
        k.cell("g = pt.genes(targets=['A'])", [], ["genes-1"])  # what the cell made stands behind its names
        self.assertEqual(k.cell("top = g.table.head()", [], []), ["genes-1"])
        k.cell("t = 0", [], [])  # assigned again: it stands for this cell's inputs only
        self.assertEqual(k.cell("t + 1", [], []), [])
        k.cell("u = t[t > 0]", ["screen"], [], run_id="run0007")  # and for the cell itself, a step of the chain
        self.assertEqual(k.cell("len(u)", [], []), ["run0007", "screen"])

    def test_a_cell_that_does_not_parse_uses_its_files_only(self):
        self.assertEqual(flow.Kernel().cell("def (", ["screen"], []), ["screen"])

    def test_looking_is_not_changing_and_magics_parse(self):
        k = flow.Kernel()
        k.cell("adata = load()", ["screen"], [], run_id="run0001")
        k.cell("print(adata.obs.head())", ["deposit"], [], run_id="run0002")  # looked at, not changed
        self.assertEqual(k.cell("adata.shape", [], []), ["run0001", "screen"])
        k.cell("%time m = adata.X.mean()", [], [], run_id="run0003")  # a magic: the line is still followed
        self.assertEqual(k.cell("m + 1", [], []), ["run0001", "run0003", "screen"])


def chain_record(folder):
    """A record as a session leaves it: the base; a cell keeping the cell set pairs; a module on it whose table a
    cell reads to save table:fit; a cell calling a block on pairs after reading table:fit; a finding on that block and
    one on the agent's own table; a result led by the first."""
    r = rc.Record(folder)
    T, A = rc.TOOL, rc.AGENT
    for oid, uses in (("deposit", []), ("design", ["deposit"]), ("screen", ["deposit", "design"])):
        r.append("object_registered", {"object_id": oid, "kind": oid, "run_id": None, "path": os.path.join(folder, oid),
                                       "uses": uses}, T)
    r.append("question_planned", {"question_id": "E.combinations", "letter": "E", "item": "pairs", "because": "x"},
             rc.PLAN)
    r.append("run_logged", {"run_id": "run0001", "what": "keep the pairs' cells", "code": "", "inputs": []}, T)
    r.append("run_finished", {"run_id": "run0001", "state": "ok", "output": "", "uses": ["screen"]}, T)
    r.append("cell_set_saved", {"name": "pairs", "run_id": "run0001", "cells": 10, "calls": True}, T)
    r.append("run_logged", {"run_id": "jobA", "job_id": "jobA", "what": 'module E.combinations with {"cells": '
                            '"pairs"}', "code": "", "inputs": [], "uses": ["screen", "cells:pairs"]}, T)
    r.append("run_finished", {"run_id": "jobA", "state": "done", "output": ""}, T)
    r.append("object_registered", {"object_id": "jobA:result", "kind": "table", "run_id": "jobA",
                                   "path": os.path.join(folder, "jobA", "result.tsv")}, T)
    r.append("run_logged", {"run_id": "run0002", "what": "fit each pair against its members", "code": "",
                            "inputs": []}, T)
    r.append("run_finished", {"run_id": "run0002", "state": "ok", "output": "", "uses": ["jobA:result"]}, T)
    r.append("object_registered", {"object_id": "table:fit", "kind": "table", "run_id": "run0002",
                                   "path": os.path.join(folder, "fit.tsv.gz"), "uses": ["jobA:result"]}, T)
    r.append("run_logged", {"run_id": "run0003", "what": "genes of the strongest pair", "code": "", "inputs": []}, T)
    r.append("run_finished", {"run_id": "run0003", "state": "ok", "output": "", "uses": ["table:fit"]}, T)
    r.append("block_called", {"call_id": "genes-abc", "block": "genes", "choices": {"method": "wilcoxon"},
                              "cells": "pairs", "kept_cells": True, "run_id": "run0003"}, T)
    for fid, runs, call in (("f1", ["run0003"], "genes-abc"), ("f2", ["run0002"], None), ("f3", ["run0001"], None)):
        r.append("finding_recorded", {"finding_id": fid, "question_id": "E.combinations", "target": "A|B",
                                      "feature": "G1", "direction": "up", "population": "K562", "run_ids": runs,
                                      "block_call": call}, A)
    r.append("dossier_proposed", {"dossier_id": "d1", "title": "A pair", "lead_findings": ["f1"],
                                  "main_run": "run0003", "map_size": {}}, A)
    r.append("dossier_chosen", {"dossier_ids": ["d1"]}, rc.USER)
    return r


class TestChains(unittest.TestCase):
    def test_a_results_chain_goes_back_to_the_deposit_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            st = chain_record(d).state()
            objs = flow.objects(st)
            self.assertEqual({objs[o]["kind"] for o in ("screen", "jobA:result", "table:fit", "cells:pairs",
                                                        "genes-abc")},
                             {"screen", "table", "cell_set", "block_result"})
            steps = flow.chain(st, flow.starts(st, ["d1"]))
            self.assertEqual([s["step"] for s in steps],
                             ["deposit", "design", "screen", "run0001", "jobA", "run0002", "genes-abc", "run0003"])
            self.assertEqual([s["by"] for s in steps], ["pertura"] * 3 + ["agent", "pertura", "agent", "pertura",
                                                                           "agent"])
            by = {s["step"]: s for s in steps}
            self.assertEqual(by["run0001"]["makes"], ["cells:pairs"])
            self.assertEqual(by["jobA"]["uses"], ["screen", "cells:pairs"])
            self.assertEqual(by["run0003"]["uses"], ["table:fit"])  # its own block call is a step of its own
            self.assertEqual(by["genes-abc"]["uses"], ["cells:pairs"])

    def test_the_reports_steps_each_name_their_entry(self):
        with tempfile.TemporaryDirectory() as d:
            st = chain_record(d).state()
            lines = results_report.chain_lines(st, "d1", 1)
            self.assertEqual(len(lines), 9)  # the sentence on the whole chain, then its 8 steps
            self.assertIn("3 of them the agent's own code", lines[0])
            for line in lines:
                self.assertEqual(results_report.unlinked(line.strip()[2:], st), [], line)
            self.assertIn("E.combinations with on the cell set pairs", " ".join(lines))
            self.assertFalse(results_report.own_measure(st, st["findings"]["f1"]))  # a block measured it
            self.assertFalse(results_report.own_measure(st, st["findings"]["f2"]))  # from a module's table
            self.assertTrue(results_report.own_measure(st, st["findings"]["f3"]))  # from the screen, its own code


def service(d):
    """An analysis service on a record alone, for the tools that need no project files."""
    svc = AnalysisService.__new__(AnalysisService)
    svc.project = d
    svc.record = rc.Record(os.path.join(d, "record"))
    svc._screen_dir = lambda: os.path.join(d, "screen")
    svc._jobs_root = lambda: os.path.join(d, "jobs")
    return svc


class TestWhatACellWrote(unittest.TestCase):
    def test_saved_tables_files_and_names_in_their_own_case(self):
        with tempfile.TemporaryDirectory() as d:
            for f, text in (("tables/PairFit.tsv.gz", "x"), ("tables/PairFit.json", "{}"),
                            ("tables/pair_fit.csv", "a,b"), ("tables/notes.json", "[1, 2]"), ("out/Fig A.png", "p")):
                os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
                with open(os.path.join(d, f), "w") as fh:
                    fh.write(text)
            got = flow.written_objects(d, [os.path.join(d, *f.split("/")) for f in
                                           ("tables/PairFit.tsv.gz", "tables/PairFit.json", "tables/pair_fit.csv",
                                            "tables/notes.json", "out/Fig A.png")])
            self.assertEqual(sorted(got), ["file:out/Fig A.png", "file:tables/notes.json", "file:tables/pair_fit.csv",
                                           "table:PairFit"])

    def test_a_table_saved_again_keeps_its_history(self):
        with tempfile.TemporaryDirectory() as d:
            svc = service(d)
            nb = type("Notebook", (), {"dir": os.path.join(d, "nb")})()
            os.makedirs(os.path.join(nb.dir, "tables"))
            path = os.path.join(nb.dir, "tables", "t.tsv.gz")
            open(path, "w").close()
            svc.record.append("run_logged", {"run_id": "run0001", "what": "first t", "code": "", "inputs": []}, rc.TOOL)
            svc.record.append("object_registered", {"object_id": "table:t", "kind": "table", "run_id": "run0001",
                                                    "path": path}, rc.TOOL)
            self.assertEqual(svc._earlier_versions(["table:t", "screen"], nb, [path]), ["run0001", "screen"])
            self.assertEqual(svc._earlier_versions(["table:t"], nb, []), ["table:t"])  # read, not saved again

    def test_a_cells_objects_are_entered_together_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            r = rc.Record(d)
            with self.assertRaises(rc.RecordError):
                r.append_many([("object_registered", {"object_id": "table:a", "kind": "table", "run_id": None,
                                                      "path": "a"}, rc.TOOL),
                               ("object_registered", {"object_id": "table:b", "kind": "table", "run_id": "no run",
                                                      "path": "b"}, rc.TOOL)])
            self.assertEqual(r.state()["objects"], {})


class TestModulesTakeObjects(unittest.TestCase):
    def test_a_table_by_its_id_and_a_missing_column_named(self):
        with tempfile.TemporaryDirectory() as d:
            svc = service(d)
            good, bad = os.path.join(d, "effects.tsv"), os.path.join(d, "scores.tsv")
            pd.DataFrame({"target": ["A"], "score": ["P1"], "effect": [0.5]}).to_csv(good, sep="\t", index=False)
            pd.DataFrame({"target": ["A"], "effect": [0.5]}).to_csv(bad, sep="\t", index=False)
            svc.record.append("run_logged", {"run_id": "run0001", "what": "program effects", "code": "",
                                             "inputs": []}, rc.TOOL)
            for oid, path in (("table:effects", good), ("table:scores", bad)):
                svc.record.append("object_registered", {"object_id": oid, "kind": "table", "run_id": "run0001",
                                                        "path": path}, rc.TOOL)
            inputs, uses = svc._object_inputs("E.program_axis", {"effects": "table:effects"})
            self.assertEqual((inputs["effects"], uses), (good, ["table:effects"]))
            with self.assertRaises(AnalysisError) as e:
                svc._object_inputs("E.program_axis", {"effects": "table:scores"})
            self.assertIn("['score']", str(e.exception))
            with self.assertRaises(AnalysisError) as e:
                svc._object_inputs("E.program_axis", {"effects": "table:nothing"})
            self.assertIn("no object 'table:nothing'", str(e.exception))
            inputs, uses = svc._object_inputs("E.co_functional_sets", {"effects": good, "program": "P1"})
            self.assertEqual((inputs["program"], uses), ("P1", ["table:effects"]))  # a path: its object found
            modes = os.path.join(d, "de.tsv")  # E.combinations' de has its own columns
            pd.DataFrame({"gene": ["G1"], "group": ["A|B"], "avg_logFC": [1.0], "DE": [True]}).to_csv(
                modes, sep="\t", index=False)
            self.assertEqual(svc._object_inputs("E.combinations", {"de": modes})[0]["de"], modes)
            with self.assertRaises(AnalysisError) as e:
                svc._object_inputs("G.direct_targets", {"de": modes})
            self.assertIn("['target', 'effect', 'q']", str(e.exception))

    def test_a_genes_run_gives_its_test_of_every_gene_where_a_module_needs_it(self):
        """G.direct_targets given a C.genes run's changed genes reads the run's own pt.genes table, every gene
        tested: its Fisher background and its activities need the genes that did not change too."""
        with tempfile.TemporaryDirectory() as d:
            svc = service(d)
            run = os.path.join(svc._jobs_root(), "run0002")
            os.makedirs(os.path.join(run, "blocks", "outputs", "c1"))
            with open(os.path.join(run, "blocks", "calls.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"call_id": "c1", "block": "genes"}) + "\n")
            full = os.path.join(run, "blocks", "outputs", "c1", "table.tsv.gz")
            pd.DataFrame({"target": ["A", "A"], "gene": ["G1", "G2"], "effect": [1.0, 0.1], "p": [1e-4, 0.8],
                          "q": [1e-3, 0.9]}).to_csv(full, sep="\t", index=False)
            changed = os.path.join(run, "changed_genes.tsv")
            pd.DataFrame({"target": ["A"], "gene": ["G1"], "effect": [1.0], "q": [1e-3]}).to_csv(
                changed, sep="\t", index=False)
            svc.record.append("run_logged", {"run_id": "run0002", "what": "C.genes", "code": "", "inputs": []},
                              rc.TOOL)
            svc.record.append("object_registered", {"object_id": "run0002:changed_genes", "kind": "table",
                                                    "run_id": "run0002", "path": changed}, rc.TOOL)
            for given in ("run0002:changed_genes", "run0002"):
                inputs, uses = svc._object_inputs("G.direct_targets", {"de": given})
                self.assertEqual((inputs["de"], uses), (full, ["run0002:changed_genes"]))
            inputs, _ = svc._object_inputs("G.direct_targets", {"de": changed})  # a path: read as given
            self.assertEqual(inputs["de"], changed)


class TestPartsAreKept(unittest.TestCase):
    def test_a_sentence_that_names_no_entry_is_listed_not_refused(self):
        with tempfile.TemporaryDirectory() as d:
            svc = service(d)
            svc.record = chain_record(os.path.join(d, "record"))
            out = svc.record_dossier_part("d1", "effect", "A|B raises G1 [run:run0003]. It is a strong effect.",
                                          run_ids=["run0003"])
            self.assertEqual(out["unlinked"], ["It is a strong effect."])
            self.assertIn("effect", svc.record.state()["dossiers"]["d1"]["parts"])


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestANotebookJoinsTheFlow(unittest.TestCase):
    def test_a_cells_inputs_and_outputs_are_entered_with_nothing_declared(self):
        from test_design_service import answer, make_deposit, proposal
        from pertura import design_gate, screen_object
        with tempfile.TemporaryDirectory() as d:
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                for tool, args, elicit in (("open_project", {"data_folder": deposit}, None), ("measure", {}, None),
                                           ("check_design", {"proposal": proposal()}, None),
                                           ("confirm_design", {}, answer())):
                    ok, out = svc.call(tool, args, elicit=elicit)
                    self.assertTrue(ok, out)
                design = design_gate.load(svc.design.design_file)[0]
                screen_object.build(deposit, design, svc._screen_dir())
                cells = ("import pandas as pd\n"
                         "t = pd.read_csv(os.path.join(screen, 'cells.tsv.gz'), sep='\\t')",
                         "n = t.groupby('label').size().rename('cells').reset_index()\n"
                         "pt.save_table('cells_per_label', n, note='cells per label')",
                         "m = pt.load('table:cells_per_label')\nprint(len(m))")
                runs = []
                for code in cells:
                    ok, run = svc.call("run_code", {"code": code, "what": "count the cells per label"})
                    self.assertTrue(ok, run)
                    self.assertEqual(run["status"], "ok", run)
                    runs.append(run)
                self.assertIn("screen", runs[0]["objects"]["used"])  # it read the screen object's cells
                self.assertIn("screen", runs[1]["objects"]["used"])  # through t, read in the cell before
                self.assertEqual(runs[1]["objects"]["made"], ["table:cells_per_label"])
                self.assertIn("table:cells_per_label", runs[2]["objects"]["used"])  # pt.load read its file
                st = svc.record.state()
                table = st["objects"]["table:cells_per_label"]
                self.assertEqual((table["run_id"], table["columns"], table["note"]),
                                 (runs[1]["run_id"], ["label", "cells"], "cells per label"))
                ok, out = svc.call("read_record", {"what": "chain", "ids": ["table:cells_per_label"]})
                self.assertTrue(ok, out)
                self.assertEqual([s["step"] for s in out["chain"]],  # the cell that read t is a step of its own
                                 ["deposit", "design", "screen", runs[0]["run_id"], runs[1]["run_id"]])
                self.assertEqual([s["by"] for s in out["chain"]], ["pertura"] * 3 + ["agent"] * 2)
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
