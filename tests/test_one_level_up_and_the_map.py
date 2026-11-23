"""Items 25, 27, 14 and 28 of docs/build_goal_session6_fixes.md. Sessions 1 to 6 stayed mostly with one target at a
time; deep screens build objects a level up (programmes, sets of regulators, states) and relate them, draw the map
and converge. Now:
- item 27: the map in the record, kept by code from the relations each finding names and those prior knowledge
  gives: each relation seen, not seen (its finding's checks disagree, or a doubt weakened it) or only known; what is
  not yet explained from the open threads and doubts; the report's last figure drawn from it;
- item 25: a role reads it and proposes threads one level up, each joining findings with the analysis that tests it;
  they come after the measured threads and before the design's; their checks: the same correlation over the
  control-guide groups, and a set's members kept together with one unit left out;
- item 14: the notebook's first reply lists the field's packages installed, by use, and those missing;
- item 28: the figure reviewer checks a result's figure as a whole against the reference standard."""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from test_design_service import make_deposit

from pertura import envs, record as rc, screen_map, threads
from pertura.analysis_service import AnalysisService
from pertura.steps import effects_across_targets as eat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        rec = self.svc.record
        for rid in ("r1", "r2"):
            rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out


class TestADoubtGoesToAWorker(Service):
    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_a_thread_that_tests_a_doubt_gives_its_workers_brief(self):
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1 [run:r1].", run_ids=["r1"],
                kind="description")
        self.ok("raise_doubt", doubt_id="d1", text="SPI1 rises with the cells' depth",
                would_change="SPI1 stays up at matched depth", finding_id="f1")
        out = self.ok("open_thread", about="SPI1 in cells matched for depth", raised_by="d1", doubt_id="d1",
                      thread_id="t-depth")
        brief = out["threads_waiting"]["briefs"]["t-depth"]["brief"]
        for said in ("doubt d1", "finding f1", "r1", "SPI1 stays up at matched depth", "worker 't-depth-doubt'"):
            self.assertIn(said, brief)
        self.assertIn("'Doubt <id>: <outcome>. <reason>'", out["threads_waiting"]["do"])  # its brief once, in the
        # main agent's notice; the doubt is answered in one line the recorder reads (docs/adr/0051)


class TestTheMap(Service):
    def test_relations_from_findings_and_knowledge_seen_not_seen_and_known(self):
        self.assertIn("sign", self.svc.call("record_finding", {
            "finding_id": "bad", "statement": "s", "run_ids": ["r1"],
            "relates": [{"from": "GATA1", "to": "SPI1", "sign": "up"}], "kind": "description"})[1]["error"])
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1 [run:r1].", run_ids=["r1"],
                relates=[{"from": "GATA1", "to": "SPI1", "sign": "-", "measured": "SPI1 +217%",
                          "from_level": "gene", "to_level": "gene"}],
                prior={"text": "GATA1 and PU.1 repress each other", "source": "PMID 10086723",
                       "relates": [{"from": "SPI1", "to": "GATA1", "sign": "-"}]}, kind="description")
        self.ok("record_finding", finding_id="f2", statement="SPI1-a does not lower GATA1 [run:r2].", run_ids=["r2"],
                relates=[{"from": "SPI1", "to": "GATA1", "sign": "-", "measured": "GATA1 +34%"}], kind="description")
        self.ok("raise_doubt", doubt_id="d1", text="GATA1 rises with the cells' size", would_change="w",
                finding_id="f2")
        self.ok("answer_doubt", doubt_id="d1", outcome="weakens", reason="it falls at matched depth", run_ids=["r2"])
        self.ok("open_thread", about="does the axis hold in G1?", raised_by="f1", thread_id="t1")
        m = self.ok("read_record", what="map")["map"]
        by = {(r["from"], r["to"]): r for r in m["relations"]}
        self.assertEqual(by[("GATA1", "SPI1")]["status"], "seen")
        self.assertEqual(by[("SPI1", "GATA1")]["status"], "not seen")  # its finding's doubt weakened it
        self.assertEqual(by[("SPI1", "GATA1")]["prior"], ["PMID 10086723"])
        self.assertIn({"id": "GATA1", "level": "gene"}, m["objects"])
        self.assertTrue(any(u.startswith("t1:") for u in m["unexplained"]))
        path = screen_map.draw(m, os.path.join(self.tmp.name, "map.png"))
        self.assertTrue(os.path.exists(path))
        self.svc.record.append("figure_saved", {"figure_id": "r1:figure", "run_id": "r1", "paths": {"png": "r1.png"},
                                                "caption": "SPI1 in GATA1-i's cells"}, rc.TOOL)
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="r1", text="GATA1-i raises SPI1 [finding:f1].")
        out = self.ok("write_report")
        text = open(out["report"], encoding="utf-8").read()
        self.assertIn("### Map of the findings", text)
        self.assertIn("figures/map.png", text)
        self.assertEqual(out["links"]["unlinked"], [])


class TestOneLevelUp(Service):
    def test_its_threads_join_findings_name_their_test_and_come_after_the_measured_ones(self):
        self.ok("record_finding", finding_id="f1", statement="a [run:r1].", run_ids=["r1"], kind="description")
        self.ok("record_finding", finding_id="f2", statement="b [run:r2].", run_ids=["r2"], kind="description")
        rec = self.svc.record
        rec.append("thread_opened", {"thread_id": "m1", "about": "measured", "raised_by": "r1", "strength": 2.0},
                   rc.TOOL)
        rec.append("thread_opened", {"thread_id": "d1", "about": "the design's pairs", "raised_by": "design"},
                   rc.TOOL)
        self.assertIn("no finding", self.svc.call("open_thread", {"about": "x", "raised_by": "one level up",
                                                                  "joins": ["f9"]})[1]["error"])
        self.assertIn("m1 is a thread: joins names findings, and the thread this one goes on from is its raised_by",
                      self.svc.call("open_thread", {"about": "x", "raised_by": "m1", "joins": ["m1"]})[1]["error"])
        self.ok("open_thread", about="GATA1 and SPI1 act as one axis", raised_by="one level up", thread_id="up1",
                joins=["f1", "f2"], test="E.program_axis on C.programs' table, with the scores' calibration",
                tells_apart="the two programmes correlate over the control-guide groups as much")
        st = rec.state()
        self.assertEqual([t["thread_id"] for t in threads.open_threads(st)["open"]], ["m1", "up1", "d1"])
        shown = next(t for t in threads.open_threads(st)["open"] if t["thread_id"] == "up1")
        self.assertEqual((shown["joins"], shown["test"][:14]), (["f1", "f2"], "E.program_axis"))
        self.assertEqual(threads.strength_order(st), ["m1", "up1", "d1"])


def effects(groups):
    """Targets moving P1 and P2 together over two groups of targets; control-guide groups with no structure."""
    rng = np.random.default_rng(0)
    rows = []
    for i in range(12):
        x = (1 if i < 6 else -1) + rng.normal(0, .1)
        rows += [{"target": f"T{i}", "score": "P1", "effect": x}, {"target": f"T{i}", "score": "P2", "effect": x}]
    null = [{"target": f"G{k}", "score": p, "effect": rng.normal(0, .2)} for k in range(groups) for p in ("P1", "P2")]
    return pd.DataFrame(rows), pd.DataFrame(null)


class TestItsChecks(unittest.TestCase):
    def test_the_same_correlation_over_the_control_guide_groups(self):
        table, null = effects(10)
        out = eat.correlate_against_groups(table, null).set_index(["program_a", "program_b"])
        self.assertGreater(out.loc[("P1", "P2"), "r"], 0.9)
        self.assertLess(abs(out.loc[("P1", "P2"), "r_groups"]), 0.9)
        self.assertNotIn("beyond_groups", out)  # a reference beside r, not a verdict
        same = eat.correlate_against_groups(table, table.assign(target="G" + table["target"])).iloc[0]
        self.assertAlmostEqual(same["r_groups"], same["r"])  # groups that correlate as much: by construction

    def test_a_sets_members_kept_together_with_one_unit_left_out(self):
        rows = []
        for u in ("u1", "u2", "u3"):
            for i in range(8):
                for p, x in (("P1", 1 if i < 4 else -1), ("P2", -1 if i < 4 else 1), ("P3", 0.5 if i % 2 else 0)):
                    noise = 0.02 * ((7 * i + 3 * int(u[1]) + int(p[1])) % 5)  # the same on every run
                    rows.append({"target": f"T{i}", "score": p, "effect": x + noise, "unit": u})
        t = pd.DataFrame(rows)
        M = eat.effects_matrix(t.groupby(["target", "score"], as_index=False)["effect"].mean())
        got, _ = eat.clusters(M.to_numpy(), list(M.index), "hierarchical")
        kept = eat.kept_without_one(t, "unit", got)
        self.assertTrue(all(v["kept"] is not None and v["kept"] >= 0.9 for v in kept.values()), kept)
        self.assertTrue(all(v["by_chance"] < v["kept"] for v in kept.values()), kept)  # the reference beside it
        self.assertNotIn(-1, eat.kept_without_one(t, "unit", {**got, "T0": -1}))  # unassigned is no set


class TestThePackagesTheNotebookNames(unittest.TestCase):
    def test_by_use_with_what_is_missing_and_what_could_not_be_read(self):
        envs.field_packages.cache_clear()

        def versions(env, names):
            if env == "r45":
                raise RuntimeError("could not read R package versions: no Rscript here")
            return {n: ("missing" if n == "bbknn" else "1.0") for n in names}
        with mock.patch.object(envs, "versions", side_effect=versions):
            got = envs.field_packages()
        envs.field_packages.cache_clear()
        self.assertIn("decoupler 1.0", got["TF and pathway activity"]["installed"])
        self.assertIn("bbknn", got["batches"]["missing"])
        self.assertIn("R: could not read", got["not read"]["why"][0])


if __name__ == "__main__":
    unittest.main()
